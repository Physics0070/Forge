"""Artifact persistence: metadata + small content in Postgres, large content in object storage."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from forge.models import Artifact
from forge.storage import ObjectStore, sha256_hex

INLINE_LIMIT = 64 * 1024


def canonical(content: Any) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()


def create_artifact(
    db: Session,
    store: ObjectStore,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
    node_id: str | None,
    type: str,
    schema_name: str,
    content: Any,
    provenance: dict[str, Any],
    content_type: str = "application/json",
    retention_days: int | None = None,
) -> Artifact:
    if isinstance(content, (str, bytes)):
        raw = content.encode() if isinstance(content, str) else content
        stored: Any = None
        inline_text = raw.decode("utf-8", "replace") if len(raw) <= INLINE_LIMIT else None
        if inline_text is not None:
            stored = {"_text": inline_text}
    else:
        raw = canonical(content)
        stored = content if len(raw) <= INLINE_LIMIT else None
    a = Artifact(
        workspace_id=workspace_id, project_id=project_id, run_id=run_id, node_id=node_id, type=type,
        schema_name=schema_name, content=stored, content_type=content_type, size_bytes=len(raw),
        content_hash=sha256_hex(raw), provenance=provenance,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=retention_days)) if retention_days else None,
    )
    db.add(a)
    db.flush()
    if stored is None:
        key = f"artifacts/{workspace_id}/{project_id}/{a.id}"
        store.put(key, raw, content_type)
        a.storage_key = key
    return a


def load_content(store: ObjectStore, a: Artifact) -> Any:
    if a.content is not None:
        if isinstance(a.content, dict) and set(a.content) == {"_text"}:
            return a.content["_text"]
        return a.content
    if a.storage_key:
        raw = store.get(a.storage_key)
        if a.content_type == "application/json":
            return json.loads(raw)
        return raw.decode("utf-8", "replace")
    return None


def artifact_out(a: Artifact, *, include_content: bool = False, store: ObjectStore | None = None) -> dict[str, Any]:
    out = {
        "id": str(a.id), "runId": str(a.run_id) if a.run_id else None, "nodeId": a.node_id, "type": a.type,
        "schema": a.schema_name, "contentType": a.content_type, "sizeBytes": a.size_bytes, "contentHash": a.content_hash,
        "provenance": a.provenance, "createdAt": a.created_at.isoformat(),
    }
    if include_content and store is not None:
        out["content"] = load_content(store, a)
    return out
