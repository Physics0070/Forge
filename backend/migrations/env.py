from alembic import context
from sqlalchemy import create_engine, pool

from forge import models  # noqa: F401  (register tables)
from forge.config import get_settings
from forge.db import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
