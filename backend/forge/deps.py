"""Request authentication + workspace authorization.

The workspace for a request is *derived* on the server: X-Workspace-Id is only a
selector and is honoured solely if the authenticated user is a member of it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from forge import ratelimit
from forge.db import get_db
from forge.models import AuthSession, User, Workspace, WorkspaceMember
from forge.security import constant_time_equals, token_digest

SESSION_COOKIE = "forge_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    workspace_id: uuid.UUID
    role: str
    session_id: uuid.UUID
    csrf_token: str


def _unauthorized() -> HTTPException:
    return HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Sign in required."})


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == token_digest(token))
    ).first()
    if row is None:
        raise _unauthorized()
    sess, user = row
    if sess.expires_at <= datetime.now(timezone.utc) or user.disabled_at is not None:
        raise _unauthorized()

    if request.method not in SAFE_METHODS:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not constant_time_equals(supplied, sess.csrf_token):
            raise HTTPException(403, {"code": "CSRF_FAILED", "message": "Missing or invalid CSRF token."})

    memberships = db.execute(
        select(WorkspaceMember.workspace_id, WorkspaceMember.role)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user.id, Workspace.deleted_at.is_(None))
        .order_by(WorkspaceMember.created_at)
    ).all()
    if not memberships:
        raise HTTPException(403, {"code": "NO_WORKSPACE", "message": "User has no workspace."})
    allowed = {wid: role for wid, role in memberships}
    selected = memberships[0][0]
    requested = request.headers.get("x-workspace-id")
    if requested:
        try:
            req_id = uuid.UUID(requested)
        except ValueError as exc:
            raise HTTPException(400, {"code": "BAD_WORKSPACE", "message": "Invalid workspace id."}) from exc
        if req_id not in allowed:
            # same response as "does not exist": never confirm other tenants' ids
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "Workspace not found."})
        selected = req_id

    principal = Principal(user.id, user.email, selected, allowed[selected], sess.id, sess.csrf_token)
    ratelimit.check(db, "api", str(principal.user_id))
    return principal


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(404, {"code": "NOT_FOUND", "message": f"{what} not found."})
