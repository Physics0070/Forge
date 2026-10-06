"""Pin search_path on trigger functions (defence against search_path hijacking).

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER FUNCTION forge_guard_workflow_version() SET search_path = pg_catalog, pg_temp")
    op.execute("ALTER FUNCTION forge_guard_append_only() SET search_path = pg_catalog, pg_temp")


def downgrade() -> None:
    op.execute("ALTER FUNCTION forge_guard_workflow_version() RESET search_path")
    op.execute("ALTER FUNCTION forge_guard_append_only() RESET search_path")
