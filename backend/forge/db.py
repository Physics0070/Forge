from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from forge.config import get_settings


class Base(DeclarativeBase):
    pass


def engine_kwargs() -> dict:
    """Serverless / transaction-pooler friendly settings (no server-side prepared statements, no client pool)."""
    s = get_settings()
    if s.db_pooler == "transaction" or s.serverless:
        return {"poolclass": NullPool, "connect_args": {"prepare_threshold": None}}
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, future=True, **engine_kwargs())


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False, future=True)


def new_session() -> Session:
    return _sessionmaker()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on any error."""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Commits at request end, rolls back on exception."""
    with session_scope() as session:
        yield session
