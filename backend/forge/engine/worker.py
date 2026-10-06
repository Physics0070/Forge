"""Durable worker: leases jobs from Postgres, executes nodes, writes results behind a lease fence.

Guarantees
- A job is leased by at most one worker (FOR UPDATE SKIP LOCKED); the lease is renewed by a heartbeat thread.
- If a worker dies, its lease expires and the reaper requeues the node (state RUNNING -> READY); the agent loop
  resumes from its last step checkpoint, successful nodes are never re-run.
- Every result write first "fences" on (job id, worker id, status=leased): a worker that lost its lease cannot
  overwrite state (prevents duplicate execution effects).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import jsonschema
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from forge.artifacts import create_artifact
from forge.config import Settings, get_settings
from forge.db import session_scope
from forge.engine import core, executors, workspace
from forge.engine.core import emit, lock_run, run_nodes, transition, workflow_of
from forge.engine.runtime import (LeaseLost, NodeBlocked, NodeCancelled, NodeCtx, NodeLoopback, NodePaused, NodeResult,
                                  NodeSuspended, NodeWaiting, get_provider)
from forge.logging import log, redact
from forge.models import Approval, Job
from forge.registry.agents import agent_from_json, builtin_lookup
from forge.storage import get_store
from forge.workflow import retry as retry_mod
from forge.workflow.retry import ErrorClass, NodeError
from forge.workflow.state import NodeState as N, RunState as R

MAX_LEASE_RECOVERIES = 6
NODE_ERROR_TEXT = {
    ErrorClass.TRANSIENT: "a temporary infrastructure problem", ErrorClass.RATE_LIMITED: "provider rate limiting",
    ErrorClass.TIMEOUT: "a timeout", ErrorClass.PERMANENT: "a permanent error", ErrorClass.POLICY_VIOLATION: "a policy violation",
    ErrorClass.MODEL_FAILURE: "a model failure", ErrorClass.TOOL_FAILURE: "a tool failure",
    ErrorClass.VALIDATION_FAILURE: "an output validation failure", ErrorClass.BUDGET: "the budget",
}


def _fence(db: Session, job_id: uuid.UUID, worker_id: str, status: str = "done") -> None:
    r = db.execute(text("UPDATE jobs SET status=:s, finished_at=now(), leased_by=NULL WHERE id=:id AND leased_by=:w AND status='leased'"),
                   {"s": status, "id": job_id, "w": worker_id})
    if r.rowcount != 1:
        raise LeaseLost()


def resolve_agent(db: Session, workspace_id: uuid.UUID, agent_id: str | None):
    if not agent_id:
        return None
    a = builtin_lookup(agent_id)
    if a:
        return a
    from forge.models import AgentDefinitionRow

    row = db.execute(select(AgentDefinitionRow).where(AgentDefinitionRow.workspace_id == workspace_id,
                                                      AgentDefinitionRow.key == agent_id,
                                                      AgentDefinitionRow.deleted_at.is_(None))).scalar_one_or_none()
    return agent_from_json(agent_id, row.definition) if row else None


class Worker:
    def __init__(self, *, settings: Settings | None = None, worker_id: str | None = None, concurrency: int | None = None,
                 poll_interval: float = 0.4, workspace_limit: int = 8):
        self.s = settings or get_settings()
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency or self.s.worker_concurrency
        self.poll = poll_interval
        self.ws_limit = workspace_limit
        self._stop = threading.Event()
        self._hb: threading.Thread | None = None
        self.yield_at: float | None = None  # set by time-sliced (serverless) ticks

    # ------------------------------------------------------------- leasing
    def lease(self) -> dict[str, Any] | None:
        with session_scope() as db:
            row = db.execute(text("""
                UPDATE jobs SET status='leased', leased_by=:w, attempt=attempt+1,
                       lease_expires_at = now() + make_interval(secs => :lease)
                WHERE id = (
                  SELECT j.id FROM jobs j JOIN runs r ON r.id = j.run_id
                  WHERE j.status='queued' AND j.run_at <= now()
                    AND r.status = 'RUNNING' AND NOT r.pause_requested AND NOT r.cancel_requested
                    AND (SELECT count(*) FROM jobs x WHERE x.workspace_id=j.workspace_id AND x.status='leased') < :wsl
                    AND (SELECT count(*) FROM jobs x WHERE x.run_id=j.run_id AND x.status='leased')
                        < COALESCE((r.ir_snapshot->'policies'->>'maxParallelism')::int, 4)
                  ORDER BY j.run_at, j.created_at
                  FOR UPDATE OF j SKIP LOCKED LIMIT 1)
                RETURNING id, run_id, node_id, workspace_id, attempt
            """), {"w": self.id, "lease": self.s.job_lease_seconds, "wsl": self.ws_limit}).first()
            return dict(row._mapping) if row else None

    # --------------------------------------------------------- main loops
    def run_forever(self) -> None:
        log("worker_started", worker=self.id, concurrency=self.concurrency)
        self._start_heartbeat()
        pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="forge-node")
        inflight: set = set()
        try:
            while not self._stop.is_set():
                inflight = {f for f in inflight if not f.done()}
                if len(inflight) >= self.concurrency:
                    time.sleep(self.poll)
                    continue
                job = self.lease()
                if job is None:
                    time.sleep(self.poll)
                    continue
                inflight.add(pool.submit(self.process, job))
        finally:
            self._stop.set()
            pool.shutdown(wait=True, cancel_futures=False)

    def stop(self) -> None:
        self._stop.set()

    def run_until_idle(self, max_seconds: float = 120.0, parallel: int | None = None) -> int:
        """Process jobs until nothing leasable remains (used by tests and one-shot jobs). Returns jobs processed."""
        n = 0
        deadline = time.monotonic() + max_seconds
        pool = ThreadPoolExecutor(max_workers=parallel or self.concurrency)
        inflight: list = []
        try:
            while time.monotonic() < deadline:
                inflight = [f for f in inflight if not f.done()]
                job = self.lease() if len(inflight) < (parallel or self.concurrency) else None
                if job:
                    inflight.append(pool.submit(self.process, job))
                    n += 1
                    continue
                if inflight:
                    time.sleep(0.05)
                    continue
                if self._has_future_work():
                    time.sleep(0.1)
                    continue
                break
        finally:
            pool.shutdown(wait=True)
        return n

    def _has_future_work(self) -> bool:
        with session_scope() as db:
            return bool(db.execute(text("""
                SELECT 1 FROM jobs j JOIN runs r ON r.id=j.run_id
                WHERE j.status='queued' AND r.status='RUNNING' AND NOT r.pause_requested AND NOT r.cancel_requested
                  AND j.run_at <= now() + interval '30 seconds' LIMIT 1""")).first()) or bool(
                db.execute(text("SELECT 1 FROM jobs WHERE status='leased' LIMIT 1")).first())

    # ---------------------------------------------------------- heartbeat
    def _start_heartbeat(self) -> None:
        def loop() -> None:
            n = 0
            while not self._stop.is_set():
                try:
                    self.heartbeat()
                    if n % 5 == 0:
                        self.reap()
                    if n % 60 == 0:
                        self.maintenance()
                except Exception:
                    logging.getLogger("forge").exception("heartbeat failed")
                n += 1
                self._stop.wait(max(2.0, self.s.job_lease_seconds / 3))

        self._hb = threading.Thread(target=loop, daemon=True, name="forge-heartbeat")
        self._hb.start()

    def heartbeat(self) -> None:
        with session_scope() as db:
            db.execute(text("""INSERT INTO workers (id, started_at, heartbeat_at, info) VALUES (:id, now(), now(), CAST(:info AS jsonb))
                               ON CONFLICT (id) DO UPDATE SET heartbeat_at = now()"""),
                       {"id": self.id, "info": '{"concurrency": %d}' % self.concurrency})
            db.execute(text("UPDATE jobs SET lease_expires_at = now() + make_interval(secs => :l) WHERE leased_by=:w AND status='leased'"),
                       {"l": self.s.job_lease_seconds, "w": self.id})

    def reap(self) -> int:
        """Requeue nodes whose worker died (lease expired). Resumes from checkpoints; never re-runs SUCCESS nodes."""
        reaped = 0
        with session_scope() as db:
            ids = db.execute(text("SELECT id FROM jobs WHERE status='leased' AND lease_expires_at < now() FOR UPDATE SKIP LOCKED")).scalars().all()
        for jid in ids:
            with session_scope() as db:
                job = db.get(Job, jid, with_for_update=True)
                if job is None or job.status != "leased" or job.lease_expires_at is None or job.lease_expires_at > core.utcnow():
                    continue
                run = lock_run(db, job.run_id)
                rn = run_nodes(db, run.id).get(job.node_id)
                lost_worker = job.leased_by
                live = rn is not None and run.status != R.CANCELLED.value and rn.state in (N.RUNNING.value, N.READY.value)
                if live and job.attempt >= MAX_LEASE_RECOVERIES and rn.state == N.RUNNING.value:
                    job.status, job.finished_at = "dead", core.utcnow()
                    transition(db, run, rn, N.FAILED, error={"class": "transient", "code": "WORKER_LOST",
                                                           "message": "The worker repeatedly failed while running this node."})
                    core.schedule(db, run)
                elif live:
                    job.status, job.leased_by, job.lease_expires_at, job.run_at = "queued", None, None, core.utcnow()
                    if rn.state == N.RUNNING.value:  # died mid-node: resume from checkpoint
                        transition(db, run, rn, N.READY, event="WORKER_LOST", lost_worker=lost_worker)
                    else:  # leased but never started
                        emit(db, run, "WORKER_LOST", node_id=rn.node_id, status="READY", lost_worker=lost_worker)
                    emit(db, run, "NODE_REQUEUED", node_id=rn.node_id, status="READY", reason="worker lease expired")
                else:
                    job.status, job.finished_at = "dead", core.utcnow()
                reaped += 1
        if reaped:
            log("jobs_reaped", logging.WARNING, count=reaped)
        return reaped

    def maintenance(self) -> None:
        from forge import ratelimit
        from forge.engine.retention import sweep

        with session_scope() as db:
            ratelimit.prune(db)
            db.execute(text("DELETE FROM workers WHERE heartbeat_at < now() - interval '1 day'"))
        try:
            sweep()
        except Exception:
            logging.getLogger("forge").exception("retention sweep failed")

    # ------------------------------------------------------------ process
    def process(self, job: dict[str, Any]) -> None:
        run_id, node_id = job["run_id"], job["node_id"]
        try:
            ctx = self._begin(job)
        except LeaseLost:
            return
        except Exception:
            logging.getLogger("forge").exception("begin failed")
            self._release_on_crash(job)
            return
        if ctx is None:
            return
        log("node_started", run_id=str(run_id), node_id=node_id, worker=self.id, attempt=ctx.attempt)
        try:
            if ctx.repo_dir is None and self._repo_key(ctx):
                ctx.repo_dir = workspace.ensure_run_repo(ctx.workspace_id, ctx.project_id, ctx.run_id, self._repo_key(ctx),
                                                         get_store())
            result = executors.execute(ctx)
            self._validate_output(ctx, result)
        except NodeSuspended:
            self._safe(self._finish_paused, ctx, "NODE_SUSPENDED")
        except NodePaused:
            self._safe(self._finish_paused, ctx)
        except NodeCancelled:
            self._safe(self._finish_cancelled, ctx)
        except NodeBlocked as b:
            self._safe(self._finish_blocked, ctx, b)
        except NodeWaiting as w:
            self._safe(self._finish_waiting, ctx, w)
        except NodeLoopback as lb:
            self._safe(self._finish_loopback, ctx, lb)
        except executors.VerificationFailed as vf:
            self._safe(self._finish_verification_failed, ctx, vf)
        except NodeError as e:
            self._safe(self._finish_error, ctx, e)
        except LeaseLost:
            log("lease_lost", logging.WARNING, run_id=str(run_id), node_id=node_id, worker=self.id)
        except Exception as e:  # a bug / unexpected failure: do not retry blindly
            logging.getLogger("forge").exception("node crashed")
            tb = traceback.format_exc(limit=4)
            self._safe(self._finish_error, ctx, NodeError(ErrorClass.PERMANENT, "Internal error while executing node.",
                                                           detail={"exception": type(e).__name__, "technical": tb[-800:]}))
        else:
            self._safe(self._finish_success, ctx, result)

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except LeaseLost:
            log("lease_lost_on_finish", logging.WARNING, worker=self.id)
        except Exception:
            logging.getLogger("forge").exception("finish handler failed")

    def _release_on_crash(self, job: dict[str, Any]) -> None:
        with session_scope() as db:
            db.execute(text("UPDATE jobs SET status='queued', leased_by=NULL, run_at = now() + interval '5 seconds' WHERE id=:id AND leased_by=:w AND status='leased'"),
                       {"id": job["id"], "w": self.id})

    @staticmethod
    def _repo_key(ctx: NodeCtx) -> str | None:
        return getattr(ctx, "_repo_key", None)

    # --------------------------------------------------------------- begin
    def _begin(self, job: dict[str, Any]) -> NodeCtx | None:
        with session_scope() as db:
            run = lock_run(db, job["run_id"])
            j = db.get(Job, job["id"])
            if j is None or j.status != "leased" or j.leased_by != self.id:
                raise LeaseLost()
            if run.status == R.CANCELLED.value or run.cancel_requested:
                j.status, j.finished_at = "dead", core.utcnow()
                return None
            if run.status != R.RUNNING.value or run.pause_requested:
                j.status, j.leased_by, j.run_at = "queued", None, core.utcnow() + timedelta(seconds=2)
                return None
            rn = run_nodes(db, run.id)[job["node_id"]]
            if rn.state != N.READY.value:
                j.status, j.finished_at = "done", core.utcnow()
                return None
            wf = workflow_of(run)
            node = wf.node(job["node_id"])
            transition(db, run, rn, N.RUNNING, worker=self.id)
            agent = resolve_agent(db, run.workspace_id, node.agent_id)
            repo_key = None
            if run.repository_id:
                from forge.models import Repository

                repo = db.get(Repository, run.repository_id)
                repo_key = repo.storage_key if repo else None
            ctx = NodeCtx(
                settings=self.s, provider=get_provider(), worker_id=self.id, job_id=j.id, run_id=run.id,
                workspace_id=run.workspace_id, project_id=run.project_id, node=node, wf=wf, agent=agent,
                input=dict(rn.input or {}), attempt=rn.attempt, iteration=rn.iteration, feedback=rn.feedback,
                model_override=rn.model_override, run_input=dict(run.input or {}),
                replay_model=(run.replay_config or {}).get("model"), repo_dir=None,
                write_approved=core.write_approved(db, run, wf, node.id), yield_at=self.yield_at)
            ctx._repo_key = repo_key  # type: ignore[attr-defined]
            return ctx

    # -------------------------------------------------------- validation
    @staticmethod
    def _validate_output(ctx: NodeCtx, result: NodeResult) -> None:
        try:
            jsonschema.validate(result.output, ctx.node.output_schema)
        except jsonschema.ValidationError as exc:
            raise NodeError(ErrorClass.VALIDATION_FAILURE, f"Output violates the node's output schema: {exc.message[:300]}",
                            detail={"path": list(exc.absolute_path)}) from exc
        for nid, out in result.assign_failed.items():
            target = ctx.wf.node(nid)
            if target:
                try:
                    jsonschema.validate(out, target.output_schema)
                except jsonschema.ValidationError as exc:
                    raise NodeError(ErrorClass.VALIDATION_FAILURE,
                                    f"Recovered output for '{nid}' violates its schema: {exc.message[:200]}") from exc

    # ------------------------------------------------------------- finishes
    def _locked(self, db: Session, ctx: NodeCtx, *, status: str = "done"):
        run = lock_run(db, ctx.run_id)
        _fence(db, ctx.job_id, self.id, status)
        rn = run_nodes(db, run.id)[ctx.node.id]
        return run, rn

    def _finish_success(self, ctx: NodeCtx, result: NodeResult) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            inputs = [e.source for e in ctx.wf.incoming(ctx.node.id)]
            for spec in result.artifacts:
                a = create_artifact(
                    db, get_store(), workspace_id=run.workspace_id, project_id=run.project_id, run_id=run.id,
                    node_id=ctx.node.id, type=spec.type, schema_name=spec.schema_name, content=spec.content,
                    content_type=spec.content_type,
                    provenance={"producer": ctx.node.agent_id or ctx.node.type.value, "node_type": ctx.node.type.value,
                                "model": result.model, "attempt": ctx.attempt, "run_id": str(run.id), "node_id": ctx.node.id,
                                "workflow_version_id": str(run.workflow_version_id), "inputs_from": inputs, **spec.provenance_extra})
                emit(db, run, "ARTIFACT_CREATED", node_id=ctx.node.id, artifact_id=str(a.id), artifact_type=spec.type,
                     hash=a.content_hash)
            rn.model, rn.routing, rn.feedback = result.model, result.routing, None
            transition(db, run, rn, N.SUCCESS, output=result.output, flagged=result.flagged or None)
            for nid, out in result.assign_failed.items():
                target = run_nodes(db, run.id).get(nid)
                if target is not None and target.state == N.FAILED.value:
                    original = target.error
                    transition(db, run, target, N.RECOVERING, event="NODE_RECOVERING", by=ctx.node.id)
                    transition(db, run, target, N.SUCCESS, output=out, event="NODE_RECOVERED", recovered_by=ctx.node.id,
                               original_error=original)
            core.schedule(db, run)

    def _finish_error(self, ctx: NodeCtx, e: NodeError) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            d = retry_mod.decide(ctx.node.retry_policy, e.error_class, rn.attempt, validation_failures_so_far=rn.attempt)
            err = {"class": e.error_class.value, "code": e.detail.get("code") or e.error_class.value.upper(),
                   "message": e.message[:600], "cause": NODE_ERROR_TEXT.get(e.error_class, "an error"),
                   "detail": redact(e.detail), "attempt": rn.attempt}
            if d.retry:
                fb = ctx.node.retry_policy.fallback_model or ((rn.routing or {}).get("fallbackModel"))
                transition(db, run, rn, N.RETRYING, error=err, delay_s=d.delay_s, reason=d.reason, fallback=d.use_fallback)
                rn.attempt += 1
                if d.use_fallback and fb:
                    rn.model_override = fb
                if e.error_class in (ErrorClass.VALIDATION_FAILURE, ErrorClass.TOOL_FAILURE, ErrorClass.MODEL_FAILURE):
                    rn.feedback = {"reason": e.message[:400], "class": e.error_class.value}
                transition(db, run, rn, N.READY, event="NODE_REQUEUED", delay_s=d.delay_s)
                core.enqueue(db, run, rn.node_id, delay_s=d.delay_s)
                core.refresh_run(db, run)
            else:
                transition(db, run, rn, N.FAILED, error={**err, "retry_decision": d.reason})
                core.schedule(db, run)

    def _finish_verification_failed(self, ctx: NodeCtx, vf: executors.VerificationFailed) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            transition(db, run, rn, N.VERIFICATION_FAILED, output=vf.output, on_fail=vf.on_fail)
            transition(db, run, rn, N.FAILED, error={
                "class": "validation_failure", "code": "VERIFICATION_FAILED", "cause": "independent verification",
                "message": "No finding could be independently verified.", "summary": vf.output.get("summary")})
            core.schedule(db, run)

    def _finish_blocked(self, ctx: NodeCtx, b: NodeBlocked) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            transition(db, run, rn, N.BLOCKED, blocked_reason="budget",
                       error={"class": "budget", "code": "BUDGET_EXCEEDED", "message": b.decision["reason"],
                              "cause": "the budget", "detail": b.decision})
            core.refresh_run(db, run)

    def _finish_waiting(self, ctx: NodeCtx, w: NodeWaiting) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            ap = Approval(workspace_id=run.workspace_id, run_id=run.id, node_id=ctx.node.id, kind=w.kind, request=w.request)
            db.add(ap)
            db.flush()
            transition(db, run, rn, N.WAITING_APPROVAL, approval_id=str(ap.id))
            emit(db, run, "APPROVAL_REQUESTED", node_id=ctx.node.id, status="PENDING", approval_id=str(ap.id), kind=w.kind)
            core.refresh_run(db, run)

    def _finish_loopback(self, ctx: NodeCtx, lb: NodeLoopback) -> None:
        with session_scope() as db:
            run, rn = self._locked(db, ctx)
            if run.status == R.CANCELLED.value or rn.state != N.RUNNING.value:
                return
            target = run_nodes(db, run.id)[lb.target]
            rn.iteration += 1
            target.iteration += 1
            target.attempt += 1
            target.feedback = lb.feedback
            transition(db, run, rn, N.PENDING, event="RETRY_LOOPBACK", target=lb.target, iteration=rn.iteration)
            transition(db, run, target, N.READY, event="NODE_LOOPBACK", reason="RETRY gate condition not met")
            core.enqueue(db, run, lb.target)
            core.refresh_run(db, run)

    def _finish_paused(self, ctx: NodeCtx, event: str = "NODE_PAUSED") -> None:
        with session_scope() as db:
            run = lock_run(db, ctx.run_id)
            r = db.execute(text("UPDATE jobs SET status='queued', leased_by=NULL, lease_expires_at=NULL WHERE id=:id AND leased_by=:w AND status='leased'"),
                           {"id": ctx.job_id, "w": self.id})
            if r.rowcount != 1:
                raise LeaseLost()
            rn = run_nodes(db, run.id)[ctx.node.id]
            if rn.state == N.RUNNING.value:
                transition(db, run, rn, N.READY, event=event)

    def _finish_cancelled(self, ctx: NodeCtx) -> None:
        with session_scope() as db:
            db.execute(text("UPDATE jobs SET status='dead', finished_at=now(), leased_by=NULL WHERE id=:id"), {"id": ctx.job_id})


def main() -> None:
    from forge.logging import setup_logging

    setup_logging("execution-worker")
    Worker().run_forever()


if __name__ == "__main__":
    main()
