from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from forge import summary
from forge.api.projects import get_project_or_404
from forge.api.runs import _start
from forge.artifacts import artifact_out, load_content
from forge.audit import audit
from forge.config import get_settings
from forge.db import get_db
from forge.deps import Principal, get_principal, not_found
from forge.engine.runtime import get_provider, router_config
from forge.models import (AgentDefinitionRow, Artifact, Evaluation, EvaluationArm, ModelCall, RetentionPolicy, Run, Workflow,
                          WorkflowVersion)
from forge.providers.pricing import load_pricing
from forge.registry.agents import BUILTIN_AGENTS, agent_from_json
from forge.registry.tools import PERMISSIONS, TOOLS
from forge.storage import get_store
from forge.templates import security_audit
from forge.workflow.ir import Workflow as IR

router = APIRouter(prefix="/api", tags=["misc"])
_SLUG = re.compile(r"^[a-z][a-z0-9_]{2,39}$")


# --------------------------------------------------------------------- agents
class AgentBody(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    role: str = Field("", max_length=200)
    description: str = Field("", max_length=1000)
    systemContract: str = Field(min_length=20, max_length=8000)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    inputSchema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    outputSchema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    model: str | None = Field(None, max_length=200)
    routing: dict[str, Any] = Field(default_factory=dict)
    timeoutS: int = Field(180, ge=5, le=1800)
    maxSteps: int = Field(14, ge=1, le=40)


@router.get("/agents")
def list_agents(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    custom = db.execute(select(AgentDefinitionRow).where(AgentDefinitionRow.workspace_id == p.workspace_id,
                                                         AgentDefinitionRow.deleted_at.is_(None))).scalars().all()
    agents = [a.to_public() for a in BUILTIN_AGENTS.values() if a.id != "verifier"] + [
        agent_from_json(r.key, r.definition).to_public() for r in custom]
    return {"agents": agents, "tools": [{"id": t.id, "name": t.name, "description": t.description, "permissions": list(t.permissions),
                                         "sideEffects": t.side_effects, "requiresApproval": t.requires_approval} for t in TOOLS.values()],
            "permissions": PERMISSIONS}


@router.post("/agents", status_code=201)
def create_agent(body: AgentBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not _SLUG.match(body.id) or body.id in BUILTIN_AGENTS:
        raise HTTPException(422, {"code": "BAD_AGENT_ID", "message": "Use 3-40 chars [a-z0-9_] starting with a letter; built-in ids are reserved."})
    for t in body.tools:
        if t not in TOOLS:
            raise HTTPException(422, {"code": "UNKNOWN_TOOL", "message": f"Unknown tool '{t}'."})
    for perm in body.permissions:
        if perm not in PERMISSIONS:
            raise HTTPException(422, {"code": "UNKNOWN_PERMISSION", "message": f"Unknown permission '{perm}'."})
    need = {perm for t in body.tools for perm in TOOLS[t].permissions}
    if not need <= set(body.permissions):
        raise HTTPException(422, {"code": "PERMISSION_CONFLICT", "message": f"Tools need permissions you did not grant: {sorted(need - set(body.permissions))}"})
    for label, sch in (("inputSchema", body.inputSchema), ("outputSchema", body.outputSchema)):
        try:
            jsonschema.Draft202012Validator.check_schema(sch)
        except jsonschema.SchemaError as exc:
            raise HTTPException(422, {"code": "INVALID_SCHEMA", "message": f"{label}: {exc.message}"}) from exc
    exists = db.execute(select(AgentDefinitionRow).where(AgentDefinitionRow.workspace_id == p.workspace_id, AgentDefinitionRow.key == body.id)).scalar_one_or_none()
    if exists and exists.deleted_at is None:
        raise HTTPException(409, {"code": "AGENT_EXISTS", "message": "An agent with this id already exists."})
    row = exists or AgentDefinitionRow(workspace_id=p.workspace_id, key=body.id, created_by=p.user_id, definition={})
    row.definition, row.deleted_at = body.model_dump(exclude={"id"}), None
    db.add(row)
    db.flush()
    audit(db, "agent.create", workspace_id=p.workspace_id, user_id=p.user_id, target_type="agent", target_id=body.id)
    return agent_from_json(body.id, row.definition).to_public()


@router.delete("/agents/{key}", status_code=204)
def delete_agent(key: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    row = db.execute(select(AgentDefinitionRow).where(AgentDefinitionRow.workspace_id == p.workspace_id, AgentDefinitionRow.key == key,
                                                      AgentDefinitionRow.deleted_at.is_(None))).scalar_one_or_none()
    if row is None:
        raise not_found("Agent")
    row.deleted_at = datetime.now(timezone.utc)
    audit(db, "agent.delete", workspace_id=p.workspace_id, user_id=p.user_id, target_type="agent", target_id=key)


# ------------------------------------------------------------------ artifacts
@router.get("/artifacts")
def list_artifacts(project_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, type: str | None = None,
                   limit: int = Query(100, le=500), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Artifact).where(Artifact.workspace_id == p.workspace_id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at.desc()).limit(limit)
    if project_id:
        q = q.where(Artifact.project_id == project_id)
    if run_id:
        q = q.where(Artifact.run_id == run_id)
    if type:
        q = q.where(Artifact.type == type)
    return {"artifacts": [artifact_out(a) for a in db.execute(q).scalars()]}


def _artifact(db: Session, p: Principal, aid: uuid.UUID) -> Artifact:
    a = db.execute(select(Artifact).where(Artifact.id == aid, Artifact.workspace_id == p.workspace_id, Artifact.deleted_at.is_(None))).scalar_one_or_none()
    if a is None:
        raise not_found("Artifact")
    return a


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    a = _artifact(db, p, artifact_id)
    out = artifact_out(a, include_content=True, store=get_store())
    from forge.models import VerificationResultRow

    vr = db.execute(select(VerificationResultRow).where(VerificationResultRow.subject_artifact_id == a.id)).scalars().all()
    out["verification"] = [{"findingId": v.claim_ref, "status": v.status, "confidence": v.confidence, "reason": v.reason} for v in vr]
    return out


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    import json

    a = _artifact(db, p, artifact_id)
    c = load_content(get_store(), a)
    body = c if isinstance(c, str) else json.dumps(c, indent=2, default=str)
    ext = "diff" if a.content_type == "text/x-diff" else "json"
    return Response(body, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{a.type}-{a.id}.{ext}"',
                                                                            "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ws = p.workspace_id
    counts = dict(db.execute(select(Run.status, func.count()).where(Run.workspace_id == ws).group_by(Run.status)).all())
    active = sum(counts.get(s, 0) for s in ("RUNNING", "PAUSED", "WAITING_APPROVAL", "BLOCKED", "PENDING"))
    ok, bad = counts.get("SUCCESS", 0), counts.get("FAILED", 0)
    vr = dict(db.execute(text("SELECT status, count(*) FROM verification_results WHERE workspace_id=:w GROUP BY status"), {"w": ws}).all())
    vtotal = sum(vr.values())
    tok = db.execute(select(func.coalesce(func.sum(ModelCall.total_tokens), 0)).where(ModelCall.workspace_id == ws)).scalar_one()
    cost = db.execute(select(func.sum(ModelCall.cost_usd), func.count(ModelCall.id), func.count(ModelCall.cost_usd))
                      .where(ModelCall.workspace_id == ws)).one()
    est = db.execute(select(func.count()).select_from(ModelCall).where(ModelCall.workspace_id == ws, ModelCall.cost_basis == "ESTIMATED")).scalar_one()
    avg = db.execute(text("SELECT avg(extract(epoch FROM (completed_at - started_at))) FROM runs WHERE workspace_id=:w AND status IN ('SUCCESS','FAILED') AND completed_at IS NOT NULL"), {"w": ws}).scalar_one()
    viol = db.execute(text("SELECT count(*) FROM events WHERE workspace_id=:w AND type='POLICY_BLOCKED'"), {"w": ws}).scalar_one()
    pending = db.execute(text("SELECT count(*) FROM approvals WHERE workspace_id=:w AND status='PENDING'"), {"w": ws}).scalar_one()
    total_runs = sum(counts.values())
    return {
        "empty": total_runs == 0,
        "activeRuns": active, "completedRuns": ok + bad, "totalRuns": total_runs,
        "successRate": round(ok / (ok + bad), 3) if (ok + bad) else None,
        "verificationRate": round(vr.get("VERIFIED", 0) / vtotal, 3) if vtotal else None, "verifiedFindings": vr.get("VERIFIED", 0),
        "totalTokens": int(tok), "totalCostUsd": round(float(cost[0]), 6) if cost[0] is not None else None,
        "costBasis": "UNAVAILABLE" if cost[0] is None else ("ESTIMATED" if est else "ACTUAL"),
        "costPartial": cost[2] < cost[1] and cost[0] is not None,
        "avgRunDurationS": round(float(avg), 2) if avg is not None else None,
        "policyViolations": int(viol), "pendingApprovals": int(pending),
    }


# --------------------------------------------------------------- system health
_docker_cache: dict[str, Any] = {"t": 0.0, "v": False}


def docker_ok() -> bool:
    if time.time() - _docker_cache["t"] > 60:
        from forge.tools.testrunner import docker_available

        _docker_cache.update(t=time.time(), v=docker_available())
    return bool(_docker_cache["v"])


@router.get("/system/health")
def system_health(live: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = get_settings()
    workers = db.execute(text("SELECT id, started_at, heartbeat_at, extract(epoch FROM (now()-heartbeat_at)) AS age FROM workers ORDER BY heartbeat_at DESC LIMIT 50")).all()
    q = dict(db.execute(text("SELECT status, count(*) FROM jobs WHERE status IN ('queued','leased') GROUP BY status")).all())
    oldest = db.execute(text("SELECT extract(epoch FROM (now()-min(run_at))) FROM jobs WHERE status='queued' AND run_at <= now()")).scalar_one()
    failed24 = db.execute(text("SELECT count(*) FROM events WHERE workspace_id=:w AND type='NODE_FAILED' AND ts > now() - interval '24 hours'"), {"w": p.workspace_id}).scalar_one()
    prov = db.execute(text("""SELECT count(*) AS n, count(*) FILTER (WHERE status='error') AS errs,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
            FROM model_calls WHERE workspace_id=:w AND ts > now() - interval '1 hour'"""), {"w": p.workspace_id}).one()
    errs_by = db.execute(text("SELECT error_class, count(*) FROM model_calls WHERE workspace_id=:w AND ts > now() - interval '1 hour' AND status='error' GROUP BY error_class"), {"w": p.workspace_id}).all()
    avg = db.execute(text("SELECT avg(extract(epoch FROM (completed_at - started_at))), count(*) FROM runs WHERE workspace_id=:w AND status IN ('SUCCESS','FAILED') AND completed_at > now() - interval '24 hours'"), {"w": p.workspace_id}).one()
    runs24 = dict(db.execute(text("SELECT status, count(*) FROM runs WHERE workspace_id=:w AND created_at > now() - interval '24 hours' GROUP BY status"), {"w": p.workspace_id}).all())
    out = {
        "database": {"ok": True},
        "workers": [{"id": w.id, "startedAt": w.started_at.isoformat(), "lastHeartbeatS": round(float(w.age), 1),
                     "healthy": float(w.age) < max(30, s.job_lease_seconds)} for w in workers],
        "queue": {"queued": int(q.get("queued", 0)), "leased": int(q.get("leased", 0)),
                  "oldestQueuedAgeS": round(float(oldest), 1) if oldest is not None else None},
        "provider": {"name": "nebius", "configured": bool(s.nebius_api_key), "last1h": {
            "calls": int(prov.n), "errors": int(prov.errs), "errorRate": round(prov.errs / prov.n, 3) if prov.n else None,
            "latencyP50Ms": round(float(prov.p50)) if prov.p50 is not None else None,
            "latencyP95Ms": round(float(prov.p95)) if prov.p95 is not None else None, "errorsByClass": {k or "unknown": v for k, v in errs_by}}},
        "failedNodes24h": int(failed24), "runs24h": runs24,
        "avgRunDuration24hS": round(float(avg[0]), 2) if avg[0] is not None else None,
        "sandbox": {"enabled": s.sandbox_enabled, "dockerAvailable": docker_ok()}, "tavily": {"configured": bool(s.tavily_api_key)},
        "storage": {"driver": get_store().driver},
    }
    if live:
        from forge import ratelimit

        ratelimit.check(db, "api", f"live-health:{p.user_id}")
        out["provider"]["live"] = get_provider().health_check()
    return out


# ------------------------------------------------------------------- settings
class RetentionBody(BaseModel):
    run_logs_days: int = Field(ge=1, le=3650)
    artifacts_days: int = Field(ge=1, le=3650)
    memory_days: int = Field(ge=1, le=3650)
    traces_days: int = Field(ge=1, le=3650)


@router.get("/settings")
def get_settings_view(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = get_settings()
    rp = db.get(RetentionPolicy, p.workspace_id)
    cfg = router_config(s)
    pricing = load_pricing()
    # NOTE: never returns secrets, only whether they are configured
    return {
        "environment": s.env, "role": p.role,
        "provider": {"name": "nebius", "baseUrlHost": re.sub(r"^https?://", "", s.nebius_base_url).split("/")[0],
                     "apiKeyConfigured": bool(s.nebius_api_key)},
        "models": {"nano": s.nebius_model_nano or None, "super": s.nebius_model_super or None, "ultra": s.nebius_model_ultra or None,
                   "default": s.nebius_model_default or None, "routerTiers": cfg.models},
        "pricing": [{"model": m, "inputPerMtok": v.input_per_mtok, "outputPerMtok": v.output_per_mtok, "source": v.source} for m, v in pricing.items()],
        "tavilyConfigured": bool(s.tavily_api_key), "sandbox": {"enabled": s.sandbox_enabled, "dockerAvailable": docker_ok(), "image": s.sandbox_image},
        "storage": {"driver": get_store().driver},
        "retention": {"run_logs_days": rp.run_logs_days, "artifacts_days": rp.artifacts_days, "memory_days": rp.memory_days,
                      "traces_days": rp.traces_days} if rp else None,
        "githubOAuthConfigured": bool(s.github_client_id and s.github_client_secret),
        "limits": {"maxZipMB": 100, "maxRepoFiles": 20000},
    }


@router.put("/settings/retention")
def put_retention(body: RetentionBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if p.role not in ("owner", "admin"):
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "Only workspace owners/admins can change retention."})
    rp = db.get(RetentionPolicy, p.workspace_id) or RetentionPolicy(workspace_id=p.workspace_id)
    for k, v in body.model_dump().items():
        setattr(rp, k, v)
    rp.updated_at = datetime.now(timezone.utc)
    db.add(rp)
    audit(db, "settings.retention", workspace_id=p.workspace_id, user_id=p.user_id, **body.model_dump())
    return body.model_dump()


@router.get("/audit-log")
def audit_log(limit: int = Query(100, le=500), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, user_id, action, target_type, target_id, metadata, ts FROM audit_log WHERE workspace_id=:w ORDER BY id DESC LIMIT :n"),
                      {"w": p.workspace_id, "n": limit}).all()
    return {"entries": [{"id": r.id, "userId": str(r.user_id) if r.user_id else None, "action": r.action, "targetType": r.target_type,
                         "targetId": r.target_id, "metadata": r.metadata, "ts": r.ts.isoformat()} for r in rows]}


# ----------------------------------------------------------------- evaluations
class EvalBody(BaseModel):
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=8, max_length=4000)
    repository_id: uuid.UUID | None = None
    arms: list[str] = Field(default_factory=lambda: ["single_agent", "forge_workflow"])
    manual_workflow_version_id: uuid.UUID | None = None
    forge_mode: str = Field("template", pattern="^(template|compile)$")
    budget_usd: float | None = Field(None, ge=0)


def _single_agent_ir(objective: str) -> dict[str, Any]:
    a = BUILTIN_AGENTS["generalist"]
    return IR.from_json({
        "name": "Single agent baseline", "goal": objective, "outputs": ["solo"],
        "nodes": [{"id": "solo", "type": "AGENT", "name": "Generalist agent", "agentId": a.id, "inputSchema": a.input_schema,
                   "outputSchema": a.output_schema, "tools": list(a.tools), "permissions": list(a.permissions),
                   "routing": {"complexity": "high", "risk": "medium"}, "timeoutS": a.timeout_s}],
        "policies": {"allowedPermissions": ["repository.read", "vulndb.lookup", "search.web", "artifact.read", "artifact.write"]}}).to_json()


@router.post("/evaluations", status_code=201)
def create_evaluation(body: EvalBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Runs the SAME objective against a single agent, a manual workflow and/or a FORGE-generated workflow.
    No numbers are fabricated: metrics are computed from the real runs once they exist."""
    project = get_project_or_404(db, p, body.project_id)
    from forge.api.runs import _repo_ok

    if body.repository_id:
        _repo_ok(db, p, body.repository_id, project.id)
    arms = list(dict.fromkeys(body.arms))
    if not arms or any(a not in ("single_agent", "manual_workflow", "forge_workflow") for a in arms):
        raise HTTPException(422, {"code": "BAD_ARMS", "message": "arms must be single_agent, manual_workflow and/or forge_workflow."})
    ev = Evaluation(workspace_id=p.workspace_id, project_id=project.id, name=body.name, created_by=p.user_id,
                    task={"objective": body.objective, "repositoryId": str(body.repository_id) if body.repository_id else None,
                          "forgeMode": body.forge_mode})
    db.add(ev)
    db.flush()
    run_input = {"objective": body.objective}
    for mode in arms:
        if mode == "manual_workflow":
            if not body.manual_workflow_version_id:
                raise HTTPException(422, {"code": "VERSION_REQUIRED", "message": "Choose the manual workflow version."})
            wv = db.execute(select(WorkflowVersion).where(WorkflowVersion.id == body.manual_workflow_version_id,
                                                          WorkflowVersion.workspace_id == p.workspace_id)).scalar_one_or_none()
            if wv is None:
                raise not_found("Workflow version")
        else:
            if mode == "single_agent":
                ir = _single_agent_ir(body.objective)
            elif body.forge_mode == "compile":
                from forge.compiler import CompileError, compile_goal

                db.commit()
                try:
                    ir = compile_goal(get_provider(), workspace_id=p.workspace_id, goal=body.objective, router_cfg=router_config(),
                                      constraints={"allow_writes": False}).workflow
                except CompileError as exc:
                    raise HTTPException(422, {"code": "COMPILATION_FAILED", "message": exc.reason, **exc.to_dict()}) from exc
            else:
                ir = security_audit.build(body.objective, include_remediation=False)
            wf = IR.from_json(ir)
            w = Workflow(workspace_id=p.workspace_id, project_id=project.id, name=f"[eval] {body.name} · {mode}", template_key="eval",
                         created_by=p.user_id)
            db.add(w)
            db.flush()
            wv = WorkflowVersion(workspace_id=p.workspace_id, workflow_id=w.id, version=1, ir=ir, ir_hash=wf.content_hash(),
                                 created_by=p.user_id, approved_by=p.user_id, approved_at=datetime.now(timezone.utc),
                                 compiler_meta={"source": "evaluation", "mode": mode})
            db.add(wv)
            db.flush()
        run, _ = _start(db, p, wv, run_input, body.repository_id, None,
                        budget_usd=body.budget_usd)
        db.add(EvaluationArm(workspace_id=p.workspace_id, evaluation_id=ev.id, mode=mode, run_id=run.id, status="RUNNING"))
    audit(db, "evaluation.create", workspace_id=p.workspace_id, user_id=p.user_id, target_type="evaluation", target_id=ev.id, arms=arms)
    db.flush()
    return eval_out(db, ev)


def eval_out(db: Session, ev: Evaluation) -> dict[str, Any]:
    arms = db.execute(select(EvaluationArm).where(EvaluationArm.evaluation_id == ev.id)).scalars().all()
    out_arms = []
    for a in arms:
        run = db.get(Run, a.run_id) if a.run_id else None
        out_arms.append({"mode": a.mode, "runId": str(a.run_id) if a.run_id else None, "status": run.status if run else a.status,
                         "metrics": summary.run_metrics(db, run) if run else None})
    return {"id": str(ev.id), "name": ev.name, "projectId": str(ev.project_id), "task": ev.task, "createdAt": ev.created_at.isoformat(),
            "arms": out_arms}


@router.get("/evaluations")
def list_evaluations(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(Evaluation).where(Evaluation.workspace_id == p.workspace_id).order_by(Evaluation.created_at.desc()).limit(50)).scalars()
    return {"evaluations": [eval_out(db, e) for e in rows]}


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ev = db.execute(select(Evaluation).where(Evaluation.id == evaluation_id, Evaluation.workspace_id == p.workspace_id)).scalar_one_or_none()
    if ev is None:
        raise not_found("Evaluation")
    return eval_out(db, ev)

