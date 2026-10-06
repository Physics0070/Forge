"""Per-run isolated repository workspace, rebuilt deterministically on any worker."""
from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

from sqlalchemy import select

from forge.artifacts import load_content
from forge.db import session_scope
from forge.models import Artifact
from forge.sandbox import extract_archive, init_baseline, run_repo_dir
from forge.storage import ObjectStore

_locks: dict[uuid.UUID, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(run_id: uuid.UUID) -> threading.Lock:
    with _guard:
        return _locks.setdefault(run_id, threading.Lock())


def _applied_file(repo: Path) -> Path:
    return repo.parent / ".applied"


def _applied_ids(repo: Path) -> set[str]:
    f = _applied_file(repo)
    return set(f.read_text().split()) if f.exists() else set()


def mark_applied(repo: Path, artifact_id: uuid.UUID) -> None:
    with open(_applied_file(repo), "a", encoding="utf-8") as fh:
        fh.write(f"{artifact_id}\n")


def _replay_patch(repo: Path, patch: str) -> None:
    from forge.patching import apply_patch
    from forge.sandbox import safe_resolve

    apply_patch(repo, patch, lambda rel: safe_resolve(repo, rel))


def ensure_run_repo(workspace_id: uuid.UUID, project_id: uuid.UUID, run_id: uuid.UUID, storage_key: str,
                    store: ObjectStore) -> Path:
    """Materialise the snapshot into the run's sandbox dir (once per worker) and replay recorded patches."""
    repo = run_repo_dir(workspace_id, project_id, run_id)
    with _lock_for(run_id):
        marker = repo.parent / ".ready"
        if not marker.exists():
            if repo.parent.exists():
                shutil.rmtree(repo.parent, ignore_errors=True)
            repo.parent.mkdir(parents=True, exist_ok=True)
            extract_archive(store.get(storage_key), repo)
            init_baseline(repo)
            marker.write_text("1")
        with session_scope() as db:
            patches = db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.type == "applied_patch")
                                 .order_by(Artifact.created_at, Artifact.id)).scalars().all()
            have = _applied_ids(repo)
            for a in patches:
                if str(a.id) not in have:
                    _replay_patch(repo, str(load_content(store, a)))
                    mark_applied(repo, a.id)
    return repo


def remove_run_dir(workspace_id: uuid.UUID, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
    shutil.rmtree(run_repo_dir(workspace_id, project_id, run_id).parent, ignore_errors=True)
    with _guard:
        _locks.pop(run_id, None)
