from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forge.audit import audit
from forge.db import get_db
from forge.deps import Principal, get_principal, not_found
from forge.models import Project, Workspace, WorkspaceMember

router = APIRouter(prefix="/api", tags=["projects"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)


def project_out(p: Project) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "settings": p.settings,
        "created_at": p.created_at.isoformat(),
    }


def get_project_or_404(db: Session, p: Principal, project_id: uuid.UUID) -> Project:
    """Every project lookup is scoped by the *authenticated* workspace."""
    proj = db.execute(
        select(Project).where(
            Project.id == project_id, Project.workspace_id == p.workspace_id, Project.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if proj is None:
        raise not_found("Project")
    return proj


@router.get("/workspaces")
def list_workspaces(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Workspace.id, Workspace.name, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == p.user_id, Workspace.deleted_at.is_(None))
    ).all()
    return {"workspaces": [{"id": str(i), "name": n, "role": r} for i, n, r in rows]}


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ws = Workspace(name=body.name.strip(), created_by=p.user_id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=p.user_id, role="owner"))
    audit(db, "workspace.create", workspace_id=ws.id, user_id=p.user_id, target_type="workspace", target_id=ws.id)
    return {"id": str(ws.id), "name": ws.name}


@router.get("/projects")
def list_projects(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Project)
        .where(Project.workspace_id == p.workspace_id, Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
    ).scalars()
    return {"projects": [project_out(x) for x in rows]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    proj = Project(
        workspace_id=p.workspace_id, name=body.name.strip(), description=body.description, created_by=p.user_id
    )
    db.add(proj)
    db.flush()
    audit(db, "project.create", workspace_id=p.workspace_id, user_id=p.user_id, target_type="project", target_id=proj.id)
    return project_out(proj)


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return project_out(get_project_or_404(db, p, project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    proj = get_project_or_404(db, p, project_id)
    proj.deleted_at = datetime.now(timezone.utc)
    audit(db, "project.delete", workspace_id=p.workspace_id, user_id=p.user_id, target_type="project", target_id=proj.id)
