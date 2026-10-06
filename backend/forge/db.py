from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from forge.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(s.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20, future=True)


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
