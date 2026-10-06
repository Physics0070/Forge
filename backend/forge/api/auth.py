from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forge import ratelimit
from forge.audit import audit
from forge.config import get_settings
from forge.db import get_db
from forge.deps import SESSION_COOKIE, Principal, get_principal
from forge.models import AuthSession, RetentionPolicy, User, Workspace, WorkspaceMember
from forge.security import hash_password, new_token, token_digest, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


class SignupBody(Credentials):
    workspace_name: str = Field("My Workspace", min_length=1, max_length=120)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))


def _start_session(db: Session, response: Response, request: Request, user: User) -> AuthSession:
    s = get_settings()
    token = new_token()
    sess = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        csrf_token=new_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(sess)
    db.flush()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.secure_cookies,
        samesite="lax",
        path="/",
    )
    return sess


def _me_payload(db: Session, user: User, csrf: str) -> dict:
    rows = db.execute(
        select(Workspace.id, Workspace.name, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.deleted_at.is_(None))
        .order_by(Workspace.created_at)
    ).all()
    return {
        "user": {"id": str(user.id), "email": user.email},
        "workspaces": [{"id": str(i), "name": n, "role": r} for i, n, r in rows],
        "csrf_token": csrf,
    }


@router.post("/signup", status_code=201)
def signup(body: SignupBody, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    ratelimit.check(db, "auth", ip)
    email = body.email.lower()
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Do not reveal whether the account exists beyond what signup inherently must.
        raise HTTPException(409, {"code": "EMAIL_TAKEN", "message": "An account with this email exists."}) from exc
    ws = Workspace(name=body.workspace_name.strip(), created_by=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.add(RetentionPolicy(workspace_id=ws.id))
    audit(db, "auth.signup", workspace_id=ws.id, user_id=user.id, ip=ip)
    sess = _start_session(db, response, request, user)
    return _me_payload(db, user, sess.csrf_token)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    ratelimit.check(db, "auth", ip)
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or user.disabled_at is not None:
        audit(db, "auth.login_failed", ip=ip, email=body.email.lower())
        db.commit()
        raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect."})
    audit(db, "auth.login", user_id=user.id, ip=ip)
    sess = _start_session(db, response, request, user)
    return _me_payload(db, user, sess.csrf_token)


@router.post("/logout", status_code=204)
def logout(response: Response, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    sess = db.get(AuthSession, p.session_id)
    if sess:
        db.delete(sess)
    audit(db, "auth.logout", workspace_id=p.workspace_id, user_id=p.user_id)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user = db.get(User, p.user_id)
    payload = _me_payload(db, user, p.csrf_token)
    payload["active_workspace_id"] = str(p.workspace_id)
    return payload
