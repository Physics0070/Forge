"""Fixed-window rate limiter backed by Postgres (works across API replicas)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

# name -> (max requests, window seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "auth": (10, 60),
    "compile": (20, 3600),
    "execute": (30, 3600),
    "api": (600, 60),
    "tool": (2000, 60),
}


def check(db: Session, scope: str, subject: str) -> None:
    limit, window = LIMITS[scope]
    now = datetime.now(timezone.utc)
    bucket = datetime.fromtimestamp(int(now.timestamp()) // window * window, tz=timezone.utc)
    key = f"{scope}:{subject}"[:200]
    count = db.execute(
        text(
            "INSERT INTO rate_limit_windows (key, window_start, count) VALUES (:k, :w, 1) "
            "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit_windows.count + 1 "
            "RETURNING count"
        ),
        {"k": key, "w": bucket},
    ).scalar_one()
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMITED", "message": f"Too many {scope} requests. Try again shortly."},
            headers={"Retry-After": str(window)},
        )


def prune(db: Session) -> int:
    res = db.execute(text("DELETE FROM rate_limit_windows WHERE window_start < now() - interval '2 hours'"))
    return res.rowcount or 0
