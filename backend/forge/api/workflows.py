from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forge import ratelimit
from forge.api.projects import get_project_or_404
from forge.audit import audit
from forge.compiler import CompileError, compile_goal
from forge.db import get_db
from forge.deps import Principal, get_principal, not_found
from forge.engine.core import emit
from forge.engine.runtime import get_provider, router_config
from forge.models import Repository, Workflow, WorkflowVersion
from forge.registry.custom import agent_lookup, workspace_agents
from forge.templates import security_audit
from forge.workflow.ir import Workflow as IR
from forge.workflow.validate import Issue, has_errors, validate_workflow

router = APIRouter(prefix="/api", tags=["workflows"])

TEMPLATES = {
    security_audit.KEY: {"key": security_audit.KEY, "name": "Repository Security Analysis & Remediation",
                         "description": "Plan -> parallel code/dependency/research scans -> prioritise -> independent verification -> "
                                        "approval-gated fixes + sandboxed tests -> report.",
                         "build": security_audit.build},
}


class CompileBody(BaseModel):
    project_id: uuid.UUID
    goal: str = Field(min_length=8, max_length=4000)
    repository_id: uuid.UUID | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class CreateWorkflowBody(BaseModel):
    project_id: uuid.UUID
    name: str | None = Field(None, max_length=160)
    ir: dict[str, Any] | None = None
    template: str | None = None
    goal: str | None = Field(None, max_length=4000)
    include_remediation: bool = True


class VersionBody(BaseModel):
    ir: dict[str, Any]
    note: str | None = Field(None, max_length=300)


class ValidateBody(BaseModel):
    ir: dict[str, Any]


def agent_lookup_for(db: Session, workspace_id: uuid.UUID):
    return agent_lookup(db, workspace_id)


def parse_ir(raw: dict[str, Any]) -> IR:
    try:
        return IR.from_json(raw)
    except ValidationError as exc:
        fields = [{"loc": [str(x) for x in e["loc"]], "message": e["msg"]} for e in exc.errors()[:12]]
        raise HTTPException(422, {"code": "INVALID_WORKFLOW_IR", "message": "Workflow does not match the IR schema.", "fields": fields}) from exc


def issues_out(issues: list[Issue]) -> list[dict[str, Any]]:
    return [i.to_dict() for i in issues]


def version_out(v: WorkflowVersion, *, ir: bool = False, issues: list[Issue] | None = None) -> dict[str, Any]:
    out = {"id": str(v.id), "version": v.version, "irHash": v.ir_hash, "approved": v.approved_at is not None,
           "approvedAt": v.approved_at.isoformat() if v.approved_at else None, "createdAt": v.created_at.isoformat(),
           "compilerMeta": v.compiler_meta, "nodeCount": len(v.ir.get("nodes", [])), "edgeCount": len(v.ir.get("edges", []))}
    if ir:
        out["ir"] = v.ir
    if issues is not None:
        out["issues"] = issues_out(issues)
    return out


def get_workflow_or_404(db: Session, p: Principal, workflow_id: uuid.UUID) -> Workflow:
    w = db.execute(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == p.workspace_id,
                                          Workflow.deleted_at.is_(None))).scalar_one_or_none()
    if w is None:
        raise not_found("Workflow")
    return w


def _versions(db: Session, wid: uuid.UUID) -> list[WorkflowVersion]:
    return list(db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wid).order_by(WorkflowVersion.version.desc())).scalars())


def workflow_out(db: Session, w: Workflow, *, with_ir: bool = False) -> dict[str, Any]:
    vs = _versions(db, w.id)
    latest = vs[0] if vs else None
    issues = None
    if latest and with_ir:
        look = agent_lookup_for(db, w.workspace_id)
        try:
            issues = validate_workflow(IR.from_json(latest.ir), agent_lookup=look)
        except ValidationError:
            issues = [Issue("error", "INVALID_IR", "Stored workflow does not parse.")]
    return {"id": str(w.id), "projectId": str(w.project_id), "name": w.name, "description": w.description,
            "templateKey": w.template_key, "createdAt": w.created_at.isoformat(),
            "latestVersion": version_out(latest, ir=with_ir, issues=issues) if latest else None,
            "versions": [version_out(v) for v in vs]}


def _store_version(db: Session, p: Principal, w: Workflow, wf: IR, *, note: str | None = None, meta: dict | None = None) -> WorkflowVersion:
    n = (db.execute(select(func.max(WorkflowVersion.version)).where(WorkflowVersion.workflow_id == w.id)).scalar_one() or 0) + 1
    now = datetime.now(timezone.utc)
    wf.id, wf.project_id, wf.version, wf.created_by = str(w.id), str(w.project_id), n, str(p.user_id)
    wf.created_at = wf.created_at or now
    wf.updated_at = now
    ir = wf.to_json()
    v = WorkflowVersion(workspace_id=p.workspace_id, workflow_id=w.id, version=n, ir=ir, ir_hash=wf.content_hash(),
                        compiler_meta={**(meta or {}), **({"note": note} if note else {})} or None, created_by=p.user_id)
    db.add(v)
    db.flush()
    audit(db, "workflow.version.create", workspace_id=p.workspace_id, user_id=p.user_id, target_type="workflow_version",
          target_id=v.id, version=n)
    return v


@router.get("/workflow-templates")
def list_templates(p: Principal = Depends(get_principal)):
    return {"templates": [{k: v for k, v in t.items() if k != "build"} for t in TEMPLATES.values()]}


@router.post("/workflows/compile")
def compile_endpoint(body: CompileBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ratelimit.check(db, "compile", str(p.user_id))
    get_project_or_404(db, p, body.project_id)
    summary = None
    if body.repository_id:
        r = db.execute(select(Repository).where(Repository.id == body.repository_id, Repository.workspace_id == p.workspace_id,
                                                 Repository.project_id == body.project_id)).scalar_one_or_none()
        if r is None:
            raise not_found("Repository")
        summary = {"fileCount": r.file_count, "sizeBytes": r.size_bytes}
    agents = workspace_agents(db, p.workspace_id)
    db.commit()  # release the row lock before the (slow) model call
    try:
        res = compile_goal(get_provider(), workspace_id=p.workspace_id, goal=body.goal, router_cfg=router_config(),
                           repo_summary=summary, constraints=body.constraints, agents=agents)
    except CompileError as exc:
        raise HTTPException(422, {"code": "COMPILATION_FAILED", **exc.to_dict(), "message": exc.reason}) from exc
    return {"workflow": res.workflow, "issues": issues_out(res.issues), "meta": res.meta}


@router.post("/workflows", status_code=201)
def create_workflow(body: CreateWorkflowBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    get_project_or_404(db, p, body.project_id)
    template_key = None
    if body.template:
        t = TEMPLATES.get(body.template)
        if t is None:
            raise HTTPException(422, {"code": "UNKNOWN_TEMPLATE", "message": f"Unknown template '{body.template}'."})
        if not body.goal:
            raise HTTPException(422, {"code": "GOAL_REQUIRED", "message": "Provide a goal for the template."})
        raw = t["build"](body.goal, include_remediation=body.include_remediation, name=body.name)
        template_key = body.template
    elif body.ir is not None:
        raw = body.ir
    else:
        raise HTTPException(422, {"code": "NOTHING_TO_CREATE", "message": "Provide `ir` or `template`."})
    wf = parse_ir(raw)
    w = Workflow(workspace_id=p.workspace_id, project_id=body.project_id, name=(body.name or wf.name)[:160],
                 description=wf.description, template_key=template_key, created_by=p.user_id)
    db.add(w)
    db.flush()
    v = _store_version(db, p, w, wf, meta={"source": "template" if template_key else "api", "template": template_key})
    emit(db, None, "WORKFLOW_CREATED", workspace_id=p.workspace_id, status="DRAFT", workflow_id=str(w.id), version=v.version)
    return workflow_out(db, w, with_ir=True)


@router.get("/workflows")
def list_workflows(project_id: uuid.UUID | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Workflow).where(Workflow.workspace_id == p.workspace_id, Workflow.deleted_at.is_(None)).order_by(Workflow.created_at.desc())
    if project_id:
        q = q.where(Workflow.project_id == project_id)
    return {"workflows": [workflow_out(db, w) for w in db.execute(q.limit(200)).scalars()]}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return workflow_out(db, get_workflow_or_404(db, p, workflow_id), with_ir=True)


@router.get("/workflows/{workflow_id}/versions/{version}")
def get_version(workflow_id: uuid.UUID, version: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    w = get_workflow_or_404(db, p, workflow_id)
    v = db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == w.id, WorkflowVersion.version == version)).scalar_one_or_none()
    if v is None:
        raise not_found("Version")
    issues = validate_workflow(IR.from_json(v.ir), agent_lookup=agent_lookup_for(db, p.workspace_id))
    return version_out(v, ir=True, issues=issues)


@router.post("/workflows/{workflow_id}/versions", status_code=201)
def new_version(workflow_id: uuid.UUID, body: VersionBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Editing never mutates a version: it always creates the next immutable one."""
    w = get_workflow_or_404(db, p, workflow_id)
    wf = parse_ir(body.ir)
    v = _store_version(db, p, w, wf, note=body.note, meta={"source": "editor"})
    issues = validate_workflow(IR.from_json(v.ir), agent_lookup=agent_lookup_for(db, p.workspace_id))
    return version_out(v, ir=True, issues=issues)


@router.post("/workflows/{workflow_id}/validate")
def validate_endpoint(workflow_id: uuid.UUID, body: ValidateBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    get_workflow_or_404(db, p, workflow_id)
    wf = parse_ir(body.ir)
    issues = validate_workflow(wf, agent_lookup=agent_lookup_for(db, p.workspace_id))
    return {"valid": not has_errors(issues), "issues": issues_out(issues)}


@router.post("/workflows/{workflow_id}/versions/{version}/approve")
def approve_version(workflow_id: uuid.UUID, version: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    w = get_workflow_or_404(db, p, workflow_id)
    v = db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == w.id, WorkflowVersion.version == version)
                   .with_for_update()).scalar_one_or_none()
    if v is None:
        raise not_found("Version")
    issues = validate_workflow(IR.from_json(v.ir), agent_lookup=agent_lookup_for(db, p.workspace_id))
    if has_errors(issues):
        raise HTTPException(422, {"code": "VALIDATION_FAILED", "message": "Fix validation errors before approving.",
                                  "issues": issues_out(issues)})
    if v.approved_at is None:
        v.approved_by, v.approved_at = p.user_id, datetime.now(timezone.utc)
        emit(db, None, "WORKFLOW_APPROVED", workspace_id=p.workspace_id, status="APPROVED", workflow_id=str(w.id), version=v.version,
             by=str(p.user_id))
        audit(db, "workflow.version.approve", workspace_id=p.workspace_id, user_id=p.user_id, target_type="workflow_version", target_id=v.id)
    return version_out(v, ir=True, issues=issues)


@router.post("/workflows/{workflow_id}/duplicate", status_code=201)
def duplicate(workflow_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    src = get_workflow_or_404(db, p, workflow_id)
    latest = _versions(db, src.id)[0]
    w = Workflow(workspace_id=p.workspace_id, project_id=src.project_id, name=f"{src.name} (copy)"[:160], description=src.description,
                 template_key=src.template_key, created_by=p.user_id)
    db.add(w)
    db.flush()
    wf = IR.from_json(latest.ir)
    wf.name = w.name
    _store_version(db, p, w, wf, meta={"source": "duplicate", "from": str(src.id)})
    return workflow_out(db, w, with_ir=True)


@router.delete("/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    w = get_workflow_or_404(db, p, workflow_id)
    w.deleted_at = datetime.now(timezone.utc)
    audit(db, "workflow.delete", workspace_id=p.workspace_id, user_id=p.user_id, target_type="workflow", target_id=w.id)


@router.get("/workflows/{workflow_id}/versions/{version}/export")
def export_version(workflow_id: uuid.UUID, version: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    w = get_workflow_or_404(db, p, workflow_id)
    v = db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == w.id, WorkflowVersion.version == version)).scalar_one_or_none()
    if v is None:
        raise not_found("Version")
    from fastapi.responses import JSONResponse

    body = {"format": "forge.workflow/1", "name": w.name, "version": v.version, "irHash": v.ir_hash, "approved": v.approved_at is not None,
            "compilerMeta": v.compiler_meta, "workflow": v.ir}
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="workflow-{w.id}-v{v.version}.json"'})
