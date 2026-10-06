"""Engine core: every state change in FORGE goes through this module (never from the frontend).

Concurrency model: each state mutation of a run happens in a transaction holding `SELECT ... FOR UPDATE`
on the run row (`lock_run`), so parallel node completions are serialised per run.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import jsonschema
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from forge.engine import readiness
from forge.models import Approval, Checkpoint, Event, Job, Run, RunNode, WorkflowVersion
from forge.workflow import paths
from forge.workflow.ir import NodeType, Workflow, WorkflowNode
from forge.workflow.state import (NODE_TERMINAL, IllegalTransition, NodeState as N, RunState as R, check_node_transition,
                                  check_run_transition)
from forge.workflow.validate import has_errors, validate_workflow

MAX_HANDOFF_RETRIES = 2
CHECKPOINT_CLIP = 120_000
_UNSET: Any = object()

_EVENT_FOR_STATE = {
    N.PENDING: "NODE_PENDING", N.READY: "NODE_READY", N.RUNNING: "NODE_STARTED", N.SUCCESS: "NODE_SUCCEEDED",
    N.FAILED: "NODE_FAILED", N.RETRYING: "RETRY_STARTED", N.BLOCKED: "NODE_BLOCKED", N.CANCELLED: "NODE_CANCELLED",
    N.SKIPPED: "NODE_SKIPPED", N.VERIFICATION_FAILED: "VERIFICATION_FAILED", N.RECOVERING: "NODE_RECOVERING",
    N.WAITING_APPROVAL: "NODE_WAITING_APPROVAL",
}


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clip_json(value: Any, limit: int = CHECKPOINT_CLIP) -> Any:
    if value is None:
        return None
    raw = json.dumps(value, default=str)
    if len(raw) <= limit:
        return value
    return {"_truncated": True, "bytes": len(raw), "preview": raw[:2000]}


# --------------------------------------------------------------------- basics
def emit(db: Session, run: Run | None, type: str, *, workspace_id: uuid.UUID | None = None, node_id: str | None = None,
         status: str | None = None, **meta: Any) -> Event:
    ev = Event(workspace_id=workspace_id or run.workspace_id, run_id=run.id if run else None, node_id=node_id, type=type,
               status=status, meta=json.loads(json.dumps(meta, default=str)))
    db.add(ev)
    db.flush()
    return ev


def lock_run(db: Session, run_id: uuid.UUID) -> Run:
    run = db.execute(select(Run).where(Run.id == run_id).with_for_update()).scalar_one_or_none()
    if run is None:
        raise EngineError("NOT_FOUND", "Run not found.")
    return run


@lru_cache(maxsize=256)
def _wf_from_hash(raw: str) -> Workflow:
    return Workflow.from_json(json.loads(raw))


def workflow_of(run: Run) -> Workflow:
    return _wf_from_hash(json.dumps(run.ir_snapshot, sort_keys=True))


def run_nodes(db: Session, run_id: uuid.UUID) -> dict[str, RunNode]:
    return {rn.node_id: rn for rn in db.execute(select(RunNode).where(RunNode.run_id == run_id)).scalars()}


def views_of(rns: dict[str, RunNode]) -> dict[str, readiness.NodeView]:
    return {k: readiness.NodeView(N(v.state), v.blocked_reason, v.output) for k, v in rns.items()}


# ----------------------------------------------------------------- transitions
def write_checkpoint(db: Session, run: Run, rn: RunNode, *, extra: dict[str, Any] | None = None) -> None:
    n_tools = db.execute(text("SELECT count(*) FROM tool_calls WHERE run_id=:r AND node_id=:n"),
                         {"r": run.id, "n": rn.node_id}).scalar_one()
    payload = {
        "workflowVersion": run.ir_snapshot.get("version"), "runId": str(run.id), "nodeId": rn.node_id,
        "input": clip_json(rn.input), "output": clip_json(rn.output), "model": rn.model, "routing": rn.routing,
        "toolCalls": n_tools, "state": rn.state, "attempt": rn.attempt, "iteration": rn.iteration,
        "timestamp": utcnow().isoformat(),
    }
    if extra:
        payload.update(extra)
    db.add(Checkpoint(workspace_id=run.workspace_id, run_id=run.id, node_id=rn.node_id, state=rn.state, payload=payload))


def transition(db: Session, run: Run, rn: RunNode, dst: N | str, *, blocked_reason: str | None = None,
               error: dict[str, Any] | None = None, output: Any = _UNSET, event: str | None = None,
               checkpoint: bool = True, **meta: Any) -> None:
    prev = N(rn.state)
    dst = check_node_transition(prev, dst)  # raises IllegalTransition
    rn.state = dst.value
    now = utcnow()
    if dst == N.RUNNING:
        rn.started_at = rn.started_at or now
        rn.finished_at = None
    if dst in NODE_TERMINAL:
        rn.finished_at = now
    rn.blocked_reason = blocked_reason if dst == N.BLOCKED else None
    if error is not None:
        rn.error = error
    elif dst == N.SUCCESS:
        rn.error = None
    if output is not _UNSET:
        rn.output = output
    emit(db, run, event or _EVENT_FOR_STATE[dst], node_id=rn.node_id, status=dst.value, previous=prev.value,
         attempt=rn.attempt, **meta)
    if checkpoint:
        write_checkpoint(db, run, rn)


def enqueue(db: Session, run: Run, node_id: str, *, delay_s: float = 0.0) -> Job | None:
    """Insert a queued job unless a live one already exists (unique partial index guards duplicates)."""
    exists = db.execute(select(Job.id).where(Job.run_id == run.id, Job.node_id == node_id,
                                             Job.status.in_(("queued", "leased")))).first()
    if exists:
        return None
    job = Job(workspace_id=run.workspace_id, run_id=run.id, node_id=node_id, status="queued",
              run_at=utcnow() + timedelta(seconds=delay_s))
    db.add(job)
    db.flush()
    return job


# ---------------------------------------------------------------- handoffs
def _errors_of(schema: dict[str, Any], data: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:160]}" for e in list(v.iter_errors(data))[:6]]


def assemble_input(wf: Workflow, node: WorkflowNode, rns: dict[str, RunNode], run_input: dict[str, Any]
                   ) -> tuple[dict[str, Any], list[str]]:
    """Build the node's typed input from live upstream outputs via edge mappings, then validate it."""
    views = views_of(rns)
    nodes = {n.id: n for n in wf.nodes}
    rp = readiness.recovery_pending_nodes(wf, views)
    incoming = wf.incoming(node.id)
    inp: dict[str, Any] = {}
    if not incoming:
        inp = dict(run_input)
    live = [e for e in incoming if e.kind == "data"
            and readiness.edge_status(e, nodes[e.source], views[e.source], rp) == "live"]
    if node.type == NodeType.JOIN:
        for e in live:
            inp[e.source] = rns[e.source].output
    else:
        unmapped = [e for e in live if not e.mapping]
        for e in live:
            out = rns[e.source].output or {}
            for m in e.mapping:
                if m.source == "$const":
                    paths.set_path(inp, m.target, m.const)
                elif m.source.startswith("$workflow"):
                    rel = m.source[len("$workflow"):].lstrip(".")
                    if paths.has_path(run_input, rel or "$"):
                        paths.set_path(inp, m.target, paths.get_path(run_input, rel or "$"))
                elif paths.has_path(out, m.source):
                    paths.set_path(inp, m.target, paths.get_path(out, m.source))
        if len(unmapped) == 1:
            for k, v in (rns[unmapped[0].source].output or {}).items():
                inp.setdefault(k, v)
        elif len(unmapped) > 1:
            for e in unmapped:
                inp[e.source] = rns[e.source].output
    # convenience: objective flows implicitly if the schema declares it
    props = (node.input_schema or {}).get("properties", {}) if isinstance(node.input_schema, dict) else {}
    if "objective" in props and "objective" not in inp and "objective" in run_input:
        inp["objective"] = run_input["objective"]
    return inp, _errors_of(node.input_schema, inp)


# ---------------------------------------------------------------- scheduler
def schedule(db: Session, run: Run) -> None:
    """Advance every PENDING node whose dependencies are resolved. Idempotent; call after any change."""
    if run.status in (R.CANCELLED.value, R.SUCCESS.value):
        return
    wf = workflow_of(run)
    for _ in range(len(wf.nodes) * 4 + 8):  # fixpoint with a hard bound
        rns = run_nodes(db, run.id)
        views = views_of(rns)
        progressed = False
        for node in wf.nodes:
            rn = rns[node.id]
            if rn.state != N.PENDING.value:
                continue
            action, reason = readiness.evaluate(wf, node, views)
            if action == "WAIT":
                continue
            if action == "SKIP":
                transition(db, run, rn, N.SKIPPED, reason=reason)
            elif action == "BLOCK":
                transition(db, run, rn, N.BLOCKED, blocked_reason=reason)
            else:
                inp, errs = assemble_input(wf, node, rns, run.input)
                if errs:
                    _handoff_failed(db, run, wf, node, rn, rns, errs)
                else:
                    rn.input = inp
                    rn.attempt = max(rn.attempt, 1)
                    transition(db, run, rn, N.READY)
                    if node.type == NodeType.RECOVERY:
                        _arm_recovery(db, run, wf, node, rns)
                    enqueue(db, run, node.id)
            progressed = True
            break  # states changed: recompute views
        if not progressed:
            break
    refresh_run(db, run)


def _arm_recovery(db: Session, run: Run, wf: Workflow, node: WorkflowNode, rns: dict[str, RunNode]) -> None:
    failed = {w: rns[w].error for w in node.config.get("watches", []) if w in rns and rns[w].state == N.FAILED.value}
    rn = rns[node.id]
    rn.input = {**(rn.input or {}), "failed": failed}


def _handoff_failed(db: Session, run: Run, wf: Workflow, node: WorkflowNode, rn: RunNode, rns: dict[str, RunNode],
                    errs: list[str]) -> None:
    emit(db, run, "HANDOFF_FAILED", node_id=node.id, status="FAILED", errors=errs,
         sources=[e.source for e in wf.incoming(node.id)])
    policies = {e.on_handoff_failure for e in wf.incoming(node.id) if e.kind == "data"}
    if "fail" not in policies and "retry_source" in policies:
        retried = False
        for e in wf.incoming(node.id):
            src = rns[e.source]
            if e.kind == "data" and src.state == N.SUCCESS.value and src.iteration < MAX_HANDOFF_RETRIES:
                src.iteration += 1
                src.attempt += 1
                src.feedback = {"reason": "Your output failed the typed handoff to the next step.", "errors": errs,
                                "previous_output": clip_json(src.output, 8000)}
                transition(db, run, src, N.READY, event="HANDOFF_RETRY")
                enqueue(db, run, src.node_id)
                retried = True
        if retried:
            return
    transition(db, run, rn, N.FAILED, error={"class": "validation_failure", "code": "HANDOFF_FAILED",
                                              "message": "Upstream output does not match this node's input schema.",
                                              "errors": errs})


def refresh_run(db: Session, run: Run) -> None:
    if run.status in (R.CANCELLED.value, R.PAUSED.value):
        return
    wf = workflow_of(run)
    outcome = readiness.run_outcome(wf, views_of(run_nodes(db, run.id)))
    target = {"ACTIVE": R.RUNNING, "WAITING_APPROVAL": R.WAITING_APPROVAL, "BLOCKED": R.BLOCKED,
              "SUCCESS": R.SUCCESS, "FAILED": R.FAILED}[outcome]
    if R(run.status) == target:
        return
    try:
        check_run_transition(run.status, target)
    except IllegalTransition:
        return
    run.status = target.value
    if target in (R.SUCCESS, R.FAILED):
        run.completed_at = utcnow()
        if target == R.FAILED:
            failed = [k for k, v in run_nodes(db, run.id).items() if v.state in (N.FAILED.value, N.BLOCKED.value)]
            run.error = {"failed_nodes": failed}
        emit(db, run, "WORKFLOW_COMPLETED", status=target.value, duration_s=_duration(run))
    else:
        run.completed_at = None
        emit(db, run, "RUN_STATUS", status=target.value)


def _duration(run: Run) -> float | None:
    if run.started_at and run.completed_at:
        return round((run.completed_at - run.started_at).total_seconds(), 3)
    return None


# -------------------------------------------------------------- run creation
def create_run(db: Session, *, wv: WorkflowVersion, project_id: uuid.UUID, created_by: uuid.UUID, input: dict[str, Any],
               repository_id: uuid.UUID | None, idempotency_key: str | None = None, budget_usd: float | None = None,
               budget_tokens: int | None = None, parent_run_id: uuid.UUID | None = None,
               replay_config: dict[str, Any] | None = None, ir_override: dict[str, Any] | None = None,
               reuse: dict[str, RunNode] | None = None) -> Run:
    if wv.approved_at is None:
        raise EngineError("NOT_APPROVED", "Workflow version must be approved before it can run.")
    ir = ir_override or wv.ir
    wf = Workflow.from_json(ir)
    from forge.registry.agents import builtin_lookup

    issues = validate_workflow(wf, agent_lookup=builtin_lookup)
    if has_errors(issues):
        raise EngineError("INVALID_WORKFLOW", "; ".join(i.message for i in issues if i.severity == "error")[:500])
    bp = wf.budget_policy
    run = Run(
        workspace_id=wv.workspace_id, project_id=project_id, workflow_version_id=wv.id, ir_snapshot=ir, status=R.RUNNING.value,
        input=input, repository_id=repository_id, parent_run_id=parent_run_id, replay_config=replay_config,
        idempotency_key=idempotency_key, budget_usd=budget_usd if budget_usd is not None else bp.max_usd,
        budget_tokens=budget_tokens if budget_tokens is not None else bp.max_tokens, created_by=created_by,
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    for n in wf.nodes:
        db.add(RunNode(run_id=run.id, workspace_id=run.workspace_id, node_id=n.id, state=N.PENDING.value, attempt=0))
    db.flush()
    emit(db, run, "RUN_CREATED", status=run.status, workflow_version=wv.version if hasattr(wv, "version") else None,
         replay_of=str(parent_run_id) if parent_run_id else None)
    if reuse:
        rns = run_nodes(db, run.id)
        for node_id, old in reuse.items():
            rn = rns.get(node_id)
            if rn is None or old.state != N.SUCCESS.value:
                continue
            rn.input, rn.output, rn.model, rn.routing = old.input, old.output, old.model, old.routing
            rn.attempt, rn.reused_from_run = 1, old.run_id
            rn.state, rn.finished_at = N.SUCCESS.value, utcnow()
            emit(db, run, "NODE_REUSED", node_id=node_id, status="SUCCESS", from_run=str(old.run_id))
            write_checkpoint(db, run, rn, extra={"reusedFrom": str(old.run_id)})
    schedule(db, run)
    return run


# -------------------------------------------------------------------- controls
def pause_run(db: Session, run_id: uuid.UUID) -> Run:
    run = lock_run(db, run_id)
    check_run_transition(run.status, R.PAUSED)
    run.pause_requested, run.status = True, R.PAUSED.value
    emit(db, run, "RUN_PAUSED", status="PAUSED")
    return run


def resume_run(db: Session, run_id: uuid.UUID) -> Run:
    run = lock_run(db, run_id)
    check_run_transition(run.status, R.RUNNING)
    run.pause_requested, run.status = False, R.RUNNING.value
    emit(db, run, "RUN_RESUMED", status="RUNNING")
    schedule(db, run)
    return run


def cancel_run(db: Session, run_id: uuid.UUID, *, reason: str = "cancelled by user") -> Run:
    run = lock_run(db, run_id)
    check_run_transition(run.status, R.CANCELLED)
    run.cancel_requested, run.status, run.completed_at = True, R.CANCELLED.value, utcnow()
    for rn in run_nodes(db, run.id).values():
        if N(rn.state) not in NODE_TERMINAL and N(rn.state) != N.FAILED:
            transition(db, run, rn, N.CANCELLED, reason=reason)
    db.execute(text("UPDATE jobs SET status='dead', finished_at=now() WHERE run_id=:r AND status IN ('queued','leased')"),
               {"r": run.id})
    for ap in db.execute(select(Approval).where(Approval.run_id == run.id, Approval.status == "PENDING")).scalars():
        ap.status, ap.decision_note, ap.decided_at = "REJECTED", "run cancelled", utcnow()
    emit(db, run, "RUN_CANCELLED", status="CANCELLED", reason=reason)
    emit(db, run, "WORKFLOW_COMPLETED", status="CANCELLED", duration_s=_duration(run))
    return run


def retry_run(db: Session, run_id: uuid.UUID, *, node_id: str | None = None) -> Run:
    """User retry: failed nodes (or one node) are re-queued; successful nodes are NOT re-run (checkpoints)."""
    run = lock_run(db, run_id)
    if run.status != R.FAILED.value:
        raise EngineError("BAD_STATE", f"Only failed runs can be retried (run is {run.status}).")
    rns = run_nodes(db, run.id)
    targets = [rns[node_id]] if node_id else [rn for rn in rns.values() if rn.state == N.FAILED.value]
    if not targets or any(t.state != N.FAILED.value for t in targets):
        raise EngineError("BAD_STATE", "No failed node to retry.")
    check_run_transition(run.status, R.RUNNING)
    run.status, run.completed_at, run.error = R.RUNNING.value, None, None
    for rn in rns.values():
        if rn.state == N.BLOCKED.value and rn.blocked_reason in ("upstream_failed", "approval_rejected"):
            # deliberate administrative reset (not a normal transition): the scheduler re-evaluates it
            rn.state, rn.blocked_reason = N.PENDING.value, None
            emit(db, run, "NODE_UNBLOCKED", node_id=rn.node_id, status="PENDING", reason="upstream retried")
    for rn in targets:
        rn.attempt += 1
        rn.feedback = None
        transition(db, run, rn, N.READY, event="NODE_RETRIED_BY_USER")
        enqueue(db, run, rn.node_id)
    emit(db, run, "RUN_RETRIED", status="RUNNING", nodes=[t.node_id for t in targets])
    schedule(db, run)
    return run


def unblock_run(db: Session, run_id: uuid.UUID, *, action: str, max_usd: float | None = None,
                max_tokens: int | None = None, model: str | None = None) -> Run:
    """Resolve a budget block: raise the budget, or re-run blocked nodes on a cheaper model."""
    run = lock_run(db, run_id)
    if run.status != R.BLOCKED.value:
        raise EngineError("BAD_STATE", f"Run is not blocked (status {run.status}).")
    if action == "increase_budget":
        if max_usd is None and max_tokens is None:
            raise EngineError("BAD_REQUEST", "Provide max_usd and/or max_tokens.")
        if max_usd is not None:
            run.budget_usd = max_usd
        if max_tokens is not None:
            run.budget_tokens = max_tokens
    elif action == "retry_with_cheaper_model":
        if not model:
            raise EngineError("BAD_REQUEST", "Provide the cheaper model id.")
    else:
        raise EngineError("BAD_REQUEST", "Unknown action.")
    check_run_transition(run.status, R.RUNNING)
    run.status = R.RUNNING.value
    for rn in run_nodes(db, run.id).values():
        if rn.state == N.BLOCKED.value and rn.blocked_reason == "budget":
            if action == "retry_with_cheaper_model":
                rn.model_override = model
            transition(db, run, rn, N.READY, event="NODE_UNBLOCKED", action=action)
            enqueue(db, run, rn.node_id)
    emit(db, run, "RUN_UNBLOCKED", status="RUNNING", action=action, max_usd=max_usd, model=model)
    schedule(db, run)
    return run


def decide_approval(db: Session, approval_id: uuid.UUID, *, workspace_id: uuid.UUID, user_id: uuid.UUID, grant: bool,
                    note: str | None = None) -> Approval:
    ap = db.get(Approval, approval_id)
    if ap is None or ap.workspace_id != workspace_id:
        raise EngineError("NOT_FOUND", "Approval not found.")
    run = lock_run(db, ap.run_id)
    db.refresh(ap)
    if ap.status != "PENDING":
        raise EngineError("BAD_STATE", f"Approval already {ap.status}.")
    if run.status in (R.CANCELLED.value, R.SUCCESS.value, R.FAILED.value):
        raise EngineError("BAD_STATE", f"Run is {run.status}.")
    rn = run_nodes(db, run.id)[ap.node_id]
    if rn.state != N.WAITING_APPROVAL.value:
        raise EngineError("BAD_STATE", "Node is not waiting for approval.")
    ap.status = "GRANTED" if grant else "REJECTED"
    ap.decided_by, ap.decided_at, ap.decision_note = user_id, utcnow(), note
    node = workflow_of(run).node(ap.node_id)
    if grant:
        emit(db, run, "APPROVAL_GRANTED", node_id=ap.node_id, status="GRANTED", approval_id=str(ap.id), by=str(user_id))
        out = {**(rn.input or {}), "approval": {"id": str(ap.id), "status": "GRANTED", "note": note,
                                                "decided_at": ap.decided_at.isoformat()}}
        transition(db, run, rn, N.SUCCESS, output=out)
    else:
        emit(db, run, "APPROVAL_REJECTED", node_id=ap.node_id, status="REJECTED", approval_id=str(ap.id), by=str(user_id))
        if node and node.config.get("on_reject") == "skip":
            transition(db, run, rn, N.SKIPPED, reason="approval rejected (skip)")
        else:
            transition(db, run, rn, N.FAILED, blocked_reason=None,
                       error={"class": "policy_violation", "code": "APPROVAL_REJECTED",
                              "message": f"Approval '{ap.kind}' was rejected.", "note": note})
    schedule(db, run)
    return ap


def write_approved(db: Session, run: Run, wf: Workflow, node_id: str) -> bool:
    """True if some APPROVAL ancestor of node_id has been GRANTED in this run (gates RepositoryWrite)."""
    from forge.workflow.validate import ancestors

    anc = [a for a in ancestors(wf, node_id) if (wf.node(a) and wf.node(a).type == NodeType.APPROVAL)]
    if not anc:
        return False
    rns = run_nodes(db, run.id)
    return any(rns[a].state == N.SUCCESS.value for a in anc)
