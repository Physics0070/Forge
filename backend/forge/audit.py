from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from forge.logging import redact
from forge.models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    ip: str | None = None,
    **meta: Any,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            meta=redact(meta),
            ip=ip,
        )
    )
