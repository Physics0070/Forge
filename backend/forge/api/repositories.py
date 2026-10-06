from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from forge import repos
from forge.api.projects import get_project_or_404
from forge.audit import audit
from forge.db import get_db, session_scope
from forge.deps import Principal, get_principal, not_found
from forge.models import Repository
from forge.sandbox import MAX_ZIP_UPLOAD
from forge.storage import get_store

router = APIRouter(prefix="/api", tags=["repositories"])


class GithubImport(BaseModel):
    url: str = Field(max_length=300)
    ref: str | None = Field(None, max_length=100)
    # Optional personal access token for a PRIVATE repo. Used transiently for the clone only:
    # never stored, never logged, never visible to agents.
    token: str | None = Field(None, max_length=300, repr=False)


def repo_out(r: Repository) -> dict:
    detail = None
    if r.status == "ready" and r.status_detail:
        try:
            detail = json.loads(r.status_detail)
        except ValueError:
            detail = None
    return {"id": str(r.id), "projectId": str(r.project_id), "kind": r.kind, "url": r.url, "ref": r.ref, "status": r.status,
            "error": r.status_detail if r.status == "failed" else None, "commitSha": r.commit_sha, "fileCount": r.file_count,
            "sizeBytes": r.size_bytes, "summary": detail, "createdAt": r.created_at.isoformat()}


def _finish(repo_id: uuid.UUID, snap: repos.Snapshot | None, error: str | None) -> None:
    with session_scope() as db:
        r = db.get(Repository, repo_id)
        if r is None:
            return
        if error or snap is None:
            r.status, r.status_detail = "failed", (error or "unknown error")[:500]
            return
        key = repos.snapshot_key(r.workspace_id, r.project_id, r.id)
        get_store().put(key, snap.archive, "application/gzip")
        r.storage_key, r.status = key, "ready"
        r.file_count, r.size_bytes, r.commit_sha = snap.file_count, snap.size_bytes, snap.commit_sha
        r.status_detail = json.dumps(repos.summarize_archive(snap.archive))


def _github_job(repo_id: uuid.UUID, url: str, ref: str | None, token: str | None) -> None:
    try:
        _finish(repo_id, repos.import_github(url, ref, token), None)
    except repos.RepoImportError as exc:
        _finish(repo_id, None, str(exc))
    except Exception:  # never leave a repo stuck in "importing"
        logging.getLogger("forge").exception("github import crashed")
        _finish(repo_id, None, "Import failed unexpectedly.")


@router.get("/projects/{project_id}/repositories")
def list_repositories(project_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    get_project_or_404(db, p, project_id)
    rows = db.execute(select(Repository).where(Repository.project_id == project_id, Repository.workspace_id == p.workspace_id,
                                                 Repository.deleted_at.is_(None)).order_by(Repository.created_at.desc())).scalars()
    return {"repositories": [repo_out(r) for r in rows]}


@router.post("/projects/{project_id}/repositories/github", status_code=202)
def import_github(project_id: uuid.UUID, body: GithubImport, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    get_project_or_404(db, p, project_id)
    try:
        repos.parse_github_url(body.url)
    except repos.RepoImportError as exc:
        raise HTTPException(422, {"code": "INVALID_REPOSITORY_URL", "message": str(exc)}) from exc
    r = Repository(workspace_id=p.workspace_id, project_id=project_id, kind="github", url=body.url.strip(), ref=body.ref,
                   status="importing")
    db.add(r)
    db.flush()
    audit(db, "repository.import", workspace_id=p.workspace_id, user_id=p.user_id, target_type="repository", target_id=r.id,
          kind="github", url=body.url, private=bool(body.token))
    db.commit()  # the background thread must see the row
    threading.Thread(target=_github_job, args=(r.id, body.url, body.ref, body.token), daemon=True, name="forge-import").start()
    return repo_out(r)


@router.post("/projects/{project_id}/repositories/zip", status_code=201)
def upload_zip(project_id: uuid.UUID, file: UploadFile = File(...), p: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    get_project_or_404(db, p, project_id)
    data = file.file.read(MAX_ZIP_UPLOAD + 1)
    r = Repository(workspace_id=p.workspace_id, project_id=project_id, kind="zip", url=(file.filename or "upload.zip")[:200],
                   status="importing")
    db.add(r)
    db.flush()
    try:
        snap = repos.import_zip(data)
    except repos.RepoImportError as exc:
        r.status, r.status_detail = "failed", str(exc)[:500]
        db.commit()
        raise HTTPException(422, {"code": "INVALID_ARCHIVE", "message": str(exc)}) from exc
    key = repos.snapshot_key(r.workspace_id, r.project_id, r.id)
    get_store().put(key, snap.archive, "application/gzip")
    r.storage_key, r.status, r.file_count, r.size_bytes = key, "ready", snap.file_count, snap.size_bytes
    r.status_detail = json.dumps(repos.summarize_archive(snap.archive))
    audit(db, "repository.import", workspace_id=p.workspace_id, user_id=p.user_id, target_type="repository", target_id=r.id, kind="zip")
    return repo_out(r)


@router.get("/repositories/{repo_id}")
def get_repository(repo_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = db.execute(select(Repository).where(Repository.id == repo_id, Repository.workspace_id == p.workspace_id,
                                            Repository.deleted_at.is_(None))).scalar_one_or_none()
    if r is None:
        raise not_found("Repository")
    return repo_out(r)


@router.delete("/repositories/{repo_id}", status_code=204)
def delete_repository(repo_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = db.execute(select(Repository).where(Repository.id == repo_id, Repository.workspace_id == p.workspace_id,
                                            Repository.deleted_at.is_(None))).scalar_one_or_none()
    if r is None:
        raise not_found("Repository")
    r.deleted_at = datetime.now(timezone.utc)
    audit(db, "repository.delete", workspace_id=p.workspace_id, user_id=p.user_id, target_type="repository", target_id=r.id)


def fail_stale_imports() -> int:
    """Called at API startup: imports interrupted by a restart must not stay 'importing' forever."""
    with session_scope() as db:
        return db.execute(text("UPDATE repositories SET status='failed', status_detail='Import interrupted by a server restart.' "
                               "WHERE status='importing' AND created_at < now() - interval '10 minutes'")).rowcount or 0
