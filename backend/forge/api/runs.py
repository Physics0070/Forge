from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forge import ratelimit, summary
from forge.artifacts import artifact_out, load_content
from forge.audit import audit
from forge.db import get_db, session_scope
from forge.deps import Principal, get_principal, not_found
from forge.engine import core
from forge.engine.core import EngineError
from forge.models import (Approval, Artifact, Checkpoint, Event, ModelCall, Repository, Run, RunNode, ToolCall, VerificationResultRow,
                          Workflow, WorkflowVersion)
from forge.storage import get_store
from forge.workflow.ir import Workflow as IR
from forge.workflow.state import IllegalTransition

router = APIRouter(prefix="/api", tags=["runs"])
TERMINAL = {"SUCCESS", "FAILED", "CANCELLED"}


class CreateRun(BaseModel):
    workflow_version_id: uuid.UUID
    input: dict[str, Any] = Field(default_factory=dict)
    repository_id: uuid.UUID | None = None
    budget_usd: float | None = Field(None, ge=0, le=100000)
    budget_tokens: int | None = Field(None, ge=1)


class ReplayBody(BaseModel):
    mode: str = Field("same", pattern="^(same|different_model|new_version)$")
    model: str | None = Field(None, max_length=200)
    workflow_version_id: uuid.UUID | None = None
    reuse_nodes: list[str] = Field(default_factory=list)
    budget_usd: float | None = Field(None, ge=0)


class UnblockBody(BaseModel):
    action: str = Field(pattern="^(increase_budget|retry_with_cheaper_model)$")
    max_usd: float | None = Field(None, ge=0)
    max_tokens: int | None = Field(None, ge=1)
    model: str | None = Field(None, max_length=200)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(grant|reject)$")
    note: str | None = Field(None, max_length=1000)


def get_run_or_404(db: Session, p: Principal, run_id: uuid.UUID) -> Run:
    r = db.execute(select(Run).where(Run.id == run_id, Run.workspace_id == p.workspace_id)).scalar_one_or_none()
    if r is None:
        raise not_found("Run")
    return r


def _engine_error(exc: EngineError | IllegalTransition) -> HTTPException:
    if isinstance(exc, IllegalTransition):
        return HTTPException(409, {"code": "ILLEGAL_STATE", "message": f"That action is not allowed while the run is {exc.src}."})
    status = {"NOT_FOUND": 404, "BAD_STATE": 409, "BAD_REQUEST": 422, "NOT_APPROVED": 409, "INVALID_WORKFLOW": 422}.get(exc.code, 400)
    return HTTPException(status, {"code": exc.code, "message": exc.message})


def _idem(db: Session, p: Principal, key: str | None) -> Run | None:
    if not key:
        return None
    return db.execute(select(Run).where(Run.workspace_id == p.workspace_id, Run.idempotency_key == key)).scalar_one_or_none()


def _repo_ok(db: Session, p: Principal, repo_id: uuid.UUID, project_id: uuid.UUID) -> Repository:
    r = db.execute(select(Repository).where(Repository.id == repo_id, Repository.workspace_id == p.workspace_id,
                                            Repository.project_id == project_id, Repository.deleted_at.is_(None))).scalar_one_or_none()
    if r is None:
        raise not_found("Repository")
    if r.status != "ready":
        raise HTTPException(409, {"code": "REPOSITORY_NOT_READY", "message": f"Repository is {r.status}."})
    return r


def _start(db: Session, p: Principal, wv: WorkflowVersion, body_input: dict, repo_id, idem: str | None, **kw) -> tuple[Run, bool]:
    existing = _idem(db, p, idem)
    if existing:
        return existing, True
    try:
        with db.begin_nested():
            run = core.create_run(db, wv=wv, project_id=db.get(Workflow, wv.workflow_id).project_id, created_by=p.user_id,
                                  input=body_input, repository_id=repo_id, idempotency_key=idem, **kw)
    except IntegrityError:
        existing = _idem(db, p, idem)
        if existing:
            return existing, True
        raise
    except (EngineError, IllegalTransition) as exc:
        raise _engine_error(exc) from exc
    audit(db, "run.create", workspace_id=p.workspace_id, user_id=p.user_id, target_type="run", target_id=run.id)
    return run, False


@router.post("/runs", status_code=201)
def create_run(body: CreateRun, response: Response, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
               idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    ratelimit.check(db, "execute", str(p.user_id))
    if idempotency_key and len(idempotency_key) > 120:
        raise HTTPException(422, {"code": "BAD_IDEMPOTENCY_KEY", "message": "Idempotency-Key is too long."})
    wv = db.execute(select(WorkflowVersion).where(WorkflowVersion.id == body.workflow_version_id,
                                                  WorkflowVersion.workspace_id == p.workspace_id)).scalar_one_or_none()
    if wv is None:
        raise not_found("Workflow version")
    wf = db.get(Workflow, wv.workflow_id)
    if wf is None or wf.deleted_at:
        raise not_found("Workflow")
    if body.repository_id:
        _repo_ok(db, p, body.repository_id, wf.project_id)
    run_input = dict(body.input)
    run_input.setdefault("objective", wv.ir.get("goal", ""))
    if not isinstance(run_input["objective"], str) or len(run_input["objective"].strip()) < 3:
        raise HTTPException(422, {"code": "OBJECTIVE_REQUIRED", "message": "Describe the objective for this run."})
    run, replay = _start(db, p, wv, run_input, body.repository_id, idempotency_key,
                         budget_usd=body.budget_usd, budget_tokens=body.budget_tokens)
    if replay:
        response.status_code = 200
    return {**summary.run_out(db, run), "idempotentReplay": replay}


@router.get("/runs")
def list_runs(project_id: uuid.UUID | None = None, status: str | None = None, limit: int = Query(50, le=200),
              p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Run).where(Run.workspace_id == p.workspace_id).order_by(Run.created_at.desc()).limit(limit)
    if project_id:
        q = q.where(Run.project_id == project_id)
    if status:
        q = q.where(Run.status == status.upper())
    return {"runs": [summary.run_out(db, r, nodes=False) for r in db.execute(q).scalars()]}


@router.get("/runs/compare")
def compare(a: uuid.UUID, b: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ra, rb = get_run_or_404(db, p, a), get_run_or_404(db, p, b)
    ma, mb = summary.run_metrics(db, ra), summary.run_metrics(db, rb)
    diffs = {}
    for k in ("success", "durationS", "totalTokens", "costUsd", "retries", "failures", "verificationRate", "artifacts", "schemaValidity",
              "policyViolations", "modelCalls"):
        va, vb = ma.get(k), mb.get(k)
        diffs[k] = {"a": va, "b": vb, "differs": va != vb,
                    "delta": (round(vb - va, 6) if isinstance(va, (int, float)) and isinstance(vb, (int, float))
                              and not isinstance(va, bool) else None)}
    return {"a": summary.run_out(db, ra, nodes=False), "b": summary.run_out(db, rb, nodes=False), "metricsA": ma, "metricsB": mb,
            "diff": diffs, "sameWorkflowVersion": ra.workflow_version_id == rb.workflow_version_id,
            "sameInput": ra.input == rb.input}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return summary.run_out(db, get_run_or_404(db, p, run_id))


@router.get("/runs/{run_id}/nodes/{node_id}")
def get_node(run_id: uuid.UUID, node_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    rn = db.execute(select(RunNode).where(RunNode.run_id == run.id, RunNode.node_id == node_id)).scalar_one_or_none()
    if rn is None:
        raise not_found("Node")
    wf = IR.from_json(run.ir_snapshot)
    node = wf.node(node_id)
    mcs = db.execute(select(ModelCall).where(ModelCall.run_id == run.id, ModelCall.node_id == node_id).order_by(ModelCall.ts)).scalars().all()
    tcs = db.execute(select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.node_id == node_id).order_by(ToolCall.ts)).scalars().all()
    arts = db.execute(select(Artifact).where(Artifact.run_id == run.id, Artifact.node_id == node_id)).scalars().all()
    vrs = db.execute(select(VerificationResultRow).where(VerificationResultRow.run_id == run.id, VerificationResultRow.node_id == node_id)).scalars().all()
    cps = db.execute(select(Checkpoint.state, Checkpoint.ts).where(Checkpoint.run_id == run.id, Checkpoint.node_id == node_id).order_by(Checkpoint.ts)).all()
    evs = db.execute(select(Event).where(Event.run_id == run.id, Event.node_id == node_id).order_by(Event.id)).scalars().all()
    return {
        **summary.node_out(rn, wf, detail=True),
        "definition": node.model_dump(by_alias=True, exclude_none=True) if node else None,
        "modelCalls": [{"id": str(m.id), "purpose": m.purpose, "model": m.model, "status": m.status, "errorClass": m.error_class,
                        "errorMessage": m.error_message, "inputTokens": m.input_tokens, "outputTokens": m.output_tokens,
                        "latencyMs": m.latency_ms, "costUsd": float(m.cost_usd) if m.cost_usd is not None else None,
                        "costBasis": m.cost_basis, "ts": m.ts.isoformat()} for m in mcs],
        "toolCalls": [{"id": str(t.id), "tool": t.tool, "operation": t.operation, "decision": t.decision, "reason": t.decision_reason,
                       "status": t.status, "error": t.error, "input": t.input, "summary": t.output_summary,
                       "durationMs": t.duration_ms, "ts": t.ts.isoformat()} for t in tcs],
        "artifacts": [artifact_out(a) for a in arts],
        "verification": [{"findingId": v.claim_ref, "status": v.status, "confidence": v.confidence, "evidenceScore": v.evidence_score,
                          "reason": v.reason, "missingEvidence": v.missing_evidence, "recommendations": v.recommendations,
                          "checks": v.checks} for v in vrs],
        "checkpoints": [{"state": s, "ts": t.isoformat()} for s, t in cps], "events": [summary.event_out(e) for e in evs],
    }


# -------------------------------------------------------------------- controls
def _control(db: Session, p: Principal, run_id: uuid.UUID, fn, audit_name: str, **kw) -> dict[str, Any]:
    get_run_or_404(db, p, run_id)
    try:
        run = fn(db, run_id, **kw)
    except (EngineError, IllegalTransition) as exc:
        raise _engine_error(exc) from exc
    audit(db, f"run.{audit_name}", workspace_id=p.workspace_id, user_id=p.user_id, target_type="run", target_id=run_id)
    return summary.run_out(db, run)


@router.post("/runs/{run_id}/pause")
def pause(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _control(db, p, run_id, core.pause_run, "pause")


@router.post("/runs/{run_id}/resume")
def resume(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _control(db, p, run_id, core.resume_run, "resume")


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _control(db, p, run_id, core.cancel_run, "cancel")


@router.post("/runs/{run_id}/retry")
def retry(run_id: uuid.UUID, node_id: str | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _control(db, p, run_id, core.retry_run, "retry", node_id=node_id)


@router.post("/runs/{run_id}/unblock")
def unblock(run_id: uuid.UUID, body: UnblockBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _control(db, p, run_id, core.unblock_run, "unblock", action=body.action, max_usd=body.max_usd,
                    max_tokens=body.max_tokens, model=body.model)


@router.post("/runs/{run_id}/replay", status_code=201)
def replay(run_id: uuid.UUID, body: ReplayBody, response: Response, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
           idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    """Create a NEW run from an immutable earlier configuration. The original run is never modified."""
    ratelimit.check(db, "execute", str(p.user_id))
    parent = get_run_or_404(db, p, run_id)
    wv_id = parent.workflow_version_id
    if body.mode == "new_version":
        if not body.workflow_version_id:
            raise HTTPException(422, {"code": "VERSION_REQUIRED", "message": "Choose the workflow version to replay with."})
        wv_id = body.workflow_version_id
    if body.mode == "different_model" and not body.model:
        raise HTTPException(422, {"code": "MODEL_REQUIRED", "message": "Choose the model to replay with."})
    wv = db.execute(select(WorkflowVersion).where(WorkflowVersion.id == wv_id, WorkflowVersion.workspace_id == p.workspace_id)).scalar_one_or_none()
    if wv is None:
        raise not_found("Workflow version")
    reuse: dict[str, RunNode] = {}
    if body.reuse_nodes:
        parent_nodes = {rn.node_id: rn for rn in db.execute(select(RunNode).where(RunNode.run_id == parent.id)).scalars()}
        old_ir, new_ir = IR.from_json(parent.ir_snapshot), IR.from_json(wv.ir)
        for nid in body.reuse_nodes:
            o, n = old_ir.node(nid), new_ir.node(nid)
            if nid in parent_nodes and o and n and o.model_dump() == n.model_dump():  # only identical node definitions are reusable
                reuse[nid] = parent_nodes[nid]
    cfg = {"mode": body.mode, "of": str(parent.id), "model": body.model if body.mode == "different_model" else None,
           "reuse_nodes": sorted(reuse)}
    existing = _idem(db, p, idempotency_key)
    if existing:
        response.status_code = 200
        return {**summary.run_out(db, existing), "idempotentReplay": True}
    run, replayed = _start(db, p, wv, dict(parent.input), parent.repository_id, idempotency_key,
                           budget_usd=body.budget_usd if body.budget_usd is not None else (float(parent.budget_usd) if parent.budget_usd is not None else None),
                           budget_tokens=parent.budget_tokens, parent_run_id=parent.id, replay_config=cfg, reuse=reuse or None)
    return {**summary.run_out(db, run), "idempotentReplay": replayed}


@router.post("/runs/{run_id}/pump")
def pump(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Serverless only: lets an open run page drive execution in short slices (the cron tick does it otherwise)."""
    from forge.config import get_settings
    from forge.engine.tick import run_tick

    if not get_settings().serverless:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Not available: dedicated workers execute runs."})
    run = get_run_or_404(db, p, run_id)
    ratelimit.check(db, "api", f"pump:{p.user_id}")
    status = run.status
    db.commit()
    if status != "RUNNING":
        return {"processed": 0, "status": status}
    out = run_tick(budget_s=40, parallel=4, label="pump")
    return {**out, "status": status}


# ---------------------------------------------------------------------- data
@router.get("/runs/{run_id}/events")
def events(run_id: uuid.UUID, after: int = 0, limit: int = Query(500, le=2000), type: str | None = None,
           p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    q = select(Event).where(Event.run_id == run.id, Event.id > after).order_by(Event.id).limit(limit)
    if type:
        q = q.where(Event.type == type)
    rows = db.execute(q).scalars().all()
    return {"events": [summary.event_out(e) for e in rows], "next": rows[-1].id if rows else after, "runStatus": run.status}


def _drain(run_id: uuid.UUID, after: int) -> tuple[list[dict[str, Any]], str]:
    with session_scope() as db:
        rows = db.execute(select(Event).where(Event.run_id == run_id, Event.id > after).order_by(Event.id).limit(200)).scalars().all()
        status = db.execute(select(Run.status).where(Run.id == run_id)).scalar_one()
        return [summary.event_out(e) for e in rows], status


def _principal_for_stream(request: Request) -> Principal:
    with session_scope() as db:  # session is released immediately; the stream must not pin a connection
        return get_principal(request, db)


@router.get("/runs/{run_id}/stream")
async def stream(run_id: uuid.UUID, request: Request, after: int = 0, p: Principal = Depends(_principal_for_stream)):
    """Server-Sent Events. The server tails the append-only event log; clients never poll."""
    def check() -> None:
        with session_scope() as db:
            get_run_or_404(db, p, run_id)

    await asyncio.to_thread(check)
    last_id = int(request.headers.get("last-event-id") or after or 0)

    async def gen():
        nonlocal last_id
        idle = 0.0
        settled_after_terminal = 0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            evs, status = await asyncio.to_thread(_drain, run_id, last_id)
            for e in evs:
                last_id = e["id"]
                yield f"id: {e['id']}\nevent: forge\ndata: {json.dumps(e)}\n\n"
            if evs:
                idle = 0.0
                settled_after_terminal = 0
            else:
                idle += 0.4
                if idle >= 15:
                    idle = 0.0
                    yield ": keepalive\n\n"
                if status in TERMINAL:
                    settled_after_terminal += 1
                    if settled_after_terminal >= 3:
                        yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                        return
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: uuid.UUID, type: str | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    q = select(Artifact).where(Artifact.run_id == run.id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at)
    if type:
        q = q.where(Artifact.type == type)
    return {"artifacts": [artifact_out(a) for a in db.execute(q).scalars()]}


@router.get("/runs/{run_id}/verification")
def run_verification(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    rows = db.execute(select(VerificationResultRow).where(VerificationResultRow.run_id == run.id)).scalars().all()
    findings: dict[str, dict] = {}
    store = get_store()
    for a in db.execute(select(Artifact).where(Artifact.run_id == run.id, Artifact.type == "security_findings")).scalars():
        c = load_content(store, a)
        for f in (c or {}).get("findings", []) if isinstance(c, dict) else []:
            findings.setdefault(f.get("id"), f)
    return {"results": [{"findingId": r.claim_ref, "status": r.status, "confidence": r.confidence, "evidenceScore": r.evidence_score,
                         "reason": r.reason, "missingEvidence": r.missing_evidence, "recommendations": r.recommendations,
                         "checks": r.checks, "finding": findings.get(r.claim_ref), "artifactId": str(r.subject_artifact_id)} for r in rows]}


@router.get("/runs/{run_id}/model-calls")
def run_model_calls(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    rows = db.execute(select(ModelCall).where(ModelCall.run_id == run.id).order_by(ModelCall.ts)).scalars().all()
    return {"modelCalls": [{"id": str(m.id), "nodeId": m.node_id, "purpose": m.purpose, "provider": m.provider, "model": m.model,
                            "status": m.status, "errorClass": m.error_class, "inputTokens": m.input_tokens,
                            "outputTokens": m.output_tokens, "latencyMs": m.latency_ms,
                            "costUsd": float(m.cost_usd) if m.cost_usd is not None else None, "costBasis": m.cost_basis,
                            "ts": m.ts.isoformat()} for m in rows]}


@router.get("/runs/{run_id}/export")
def export_run(run_id: uuid.UUID, format: str = Query("json", pattern="^(json|markdown|trace|bundle)$"),
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = get_run_or_404(db, p, run_id)
    store = get_store()
    detail = summary.run_out(db, run)
    arts = db.execute(select(Artifact).where(Artifact.run_id == run.id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at)).scalars().all()
    evs = [summary.event_out(e) for e in db.execute(select(Event).where(Event.run_id == run.id).order_by(Event.id)).scalars()]
    if format == "markdown":
        report = next((a for a in reversed(arts) if a.type == "report"), None)
        if report is None:
            raise HTTPException(404, {"code": "NO_REPORT", "message": "This run has not produced a report yet."})
        c = load_content(store, report)
        md = c.get("markdown") if isinstance(c, dict) else str(c)
        return Response(md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="report-{run.id}.md"'})
    if format == "trace":
        return JSONResponse({"format": "forge.trace/1", "runId": str(run.id), "events": evs},
                            headers={"Content-Disposition": f'attachment; filename="trace-{run.id}.json"'})
    mcs = db.execute(select(ModelCall).where(ModelCall.run_id == run.id)).scalars().all()
    tcs = db.execute(select(ToolCall).where(ToolCall.run_id == run.id)).scalars().all()
    doc = {"format": "forge.run/1", "run": detail, "workflow": run.ir_snapshot, "events": evs,
           "modelCalls": [{"node": m.node_id, "model": m.model, "tokens": m.total_tokens, "latencyMs": m.latency_ms,
                           "cost": float(m.cost_usd) if m.cost_usd is not None else None, "costBasis": m.cost_basis, "status": m.status} for m in mcs],
           "toolCalls": [{"node": t.node_id, "tool": t.tool, "decision": t.decision, "status": t.status, "input": t.input} for t in tcs],
           "artifacts": [artifact_out(a, include_content=a.size_bytes < 256_000, store=store) for a in arts],
           "reproducibility": {"workflowVersionId": detail["workflowVersionId"], "models": sorted({m.model for m in mcs}),
                               "input": run.input, "replayConfig": run.replay_config}}
    if format == "json":
        return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="run-{run.id}.json"'})
    buf = io.BytesIO()  # bundle: run.json + each artifact as a file
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("run.json", json.dumps(doc, indent=2, default=str))
        for a in arts:
            c = load_content(store, a)
            ext = "diff" if a.content_type == "text/x-diff" else "md" if a.type == "report" else "json"
            body = (c.get("markdown") if a.type == "report" and isinstance(c, dict) else c)
            z.writestr(f"artifacts/{a.type}-{a.id}.{ext}", body if isinstance(body, str) else json.dumps(body, indent=2, default=str))
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="run-{run.id}.zip"'})


# ------------------------------------------------------------------- approvals
@router.get("/approvals")
def list_approvals(status: str = "PENDING", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(Approval).where(Approval.workspace_id == p.workspace_id, Approval.status == status.upper())
                      .order_by(Approval.created_at.desc()).limit(100)).scalars().all()
    return {"approvals": [summary.approval_out(a) for a in rows]}


@router.post("/approvals/{approval_id}")
def decide(approval_id: uuid.UUID, body: ApprovalDecision, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    try:
        ap = core.decide_approval(db, approval_id, workspace_id=p.workspace_id, user_id=p.user_id, grant=body.decision == "grant",
                                  note=body.note)
    except (EngineError, IllegalTransition) as exc:
        raise _engine_error(exc) from exc
    audit(db, f"approval.{body.decision}", workspace_id=p.workspace_id, user_id=p.user_id, target_type="approval", target_id=approval_id,
          run_id=str(ap.run_id), kind=ap.kind)
    return summary.approval_out(ap)

