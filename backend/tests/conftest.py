"""Test harness: real Postgres (separate `forge_test` db), real migrations. No mocks of our own DB."""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def _base_url() -> str:
    url = os.environ.get("FORGE_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        env = ROOT / ".env"
        if env.exists():
            m = re.search(r"^DATABASE_URL=(.+)$", env.read_text(), re.M)
            url = m.group(1).strip() if m else None
    if not url:
        pytest.exit("Set FORGE_TEST_DATABASE_URL (or DATABASE_URL) to a Postgres instance for tests.", 2)
    return url


_u = make_url(_base_url()).set(database="forge_test")
TEST_DB_URL = _u.render_as_string(hide_password=False)

os.environ["FORGE_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("NEBIUS_API_KEY", "")
os.environ["TAVILY_API_KEY"] = ""


def _ensure_database() -> None:
    admin = _u.set(database="postgres", drivername="postgresql")
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname='forge_test'").fetchone()
        if not exists:
            conn.execute("CREATE DATABASE forge_test")


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    _ensure_database()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_tables(_migrated_db):
    from sqlalchemy import text

    from forge.db import get_engine

    with get_engine().begin() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")
        ).scalars().all()
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from forge.api.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c


def signup(client, email: str = "a@example.com", password: str = "correct-horse-battery"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "workspace_name": f"ws-{email}"})
    assert r.status_code == 201, r.text
    return r.json()


def auth_headers(me: dict) -> dict:
    return {"x-csrf-token": me["csrf_token"]}
