"""DB-level integrity: immutable workflow versions, append-only events.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION forge_guard_workflow_version() RETURNS trigger AS $$
        BEGIN
          IF NEW.ir IS DISTINCT FROM OLD.ir
             OR NEW.ir_hash IS DISTINCT FROM OLD.ir_hash
             OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
             OR NEW.version IS DISTINCT FROM OLD.version THEN
            RAISE EXCEPTION 'workflow_versions content is immutable; create a new version';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_workflow_version_immutable BEFORE UPDATE ON workflow_versions "
        "FOR EACH ROW EXECUTE FUNCTION forge_guard_workflow_version();"
    )
    op.execute(
        """
        CREATE FUNCTION forge_guard_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_events_append_only BEFORE UPDATE ON events "
        "FOR EACH ROW EXECUTE FUNCTION forge_guard_append_only();"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_append_only BEFORE UPDATE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION forge_guard_append_only();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_events_append_only ON events;")
    op.execute("DROP FUNCTION IF EXISTS forge_guard_append_only();")
    op.execute("DROP TRIGGER IF EXISTS trg_workflow_version_immutable ON workflow_versions;")
    op.execute("DROP FUNCTION IF EXISTS forge_guard_workflow_version();")
