"""Relational schema. Migrations (alembic) are generated from these models.

Design notes
- Every tenant-owned table carries workspace_id; the API derives it from the
  authenticated session, never from client input.
- workflow_versions are immutable (enforced by a DB trigger in migration 0002).
- `jobs` is the durable queue (SELECT ... FOR UPDATE SKIP LOCKED leasing).
- `events` is append-only; its bigserial id is the SSE cursor.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from forge.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _fk(table: str, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)


# ---------------------------------------------------------------- identity ---
class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 hex
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _ts()
    user_agent: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (Index("ix_auth_sessions_user", "user_id"),)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="owner")  # owner|admin|member
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_workspace_members_user", "user_id"),)


# ---------------------------------------------------------------- projects ---
class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_projects_workspace", "workspace_id"),)


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # github | zip
    url: Mapped[str | None] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    status_detail: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)  # object-store key of the snapshot archive
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    file_count: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_repositories_project", "project_id"),)


class AgentDefinitionRow(Base):
    """Custom agents. Built-in agents live in code and are never stored here."""

    __tablename__ = "agent_definitions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_agent_workspace_key"),)


# --------------------------------------------------------------- workflows ---
class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    template_key: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_workflows_project", "project_id"),)


class WorkflowVersion(Base):
    """Immutable. A new edit is always a new row (version+1)."""

    __tablename__ = "workflow_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    workflow_id: Mapped[uuid.UUID] = _fk("workflows")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ir_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiler_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # model, tokens, prompt hash
    approved_by: Mapped[uuid.UUID | None] = _fk("users", nullable=True, ondelete="SET NULL")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),)


# -------------------------------------------------------------------- runs ---
class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    workflow_version_id: Mapped[uuid.UUID] = _fk("workflow_versions", ondelete="RESTRICT")
    # Frozen copy of the IR + policies actually executed (replay-safe even if overrides were applied).
    ir_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="PENDING")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    repository_id: Mapped[uuid.UUID | None] = _fk("repositories", nullable=True, ondelete="SET NULL")
    parent_run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True, ondelete="SET NULL")
    replay_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    budget_tokens: Mapped[int | None] = mapped_column(BigInteger)
    spent_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    spent_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_run_idempotency"),
        Index("ix_runs_workspace_created", "workspace_id", "created_at"),
        Index("ix_runs_status", "status"),
    )


class RunNode(Base):
    __tablename__ = "run_nodes"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = _fk("runs")
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, server_default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model: Mapped[str | None] = mapped_column(String(200))
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # tier, reason, fallback
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # RETRY-node loop count
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # why the node is being re-run
    model_override: Mapped[str | None] = mapped_column(String(200))
    blocked_reason: Mapped[str | None] = mapped_column(String(40))  # budget | upstream_failed | approval_rejected
    reused_from_run: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("run_id", "node_id", name="uq_run_node"),
        Index("ix_run_nodes_run", "run_id"),
    )


class Job(Base):
    """Durable queue. One row = one node execution attempt that must run."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID] = _fk("runs")
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, server_default="node")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")  # queued|leased|done|dead
    run_at: Mapped[datetime] = _ts()
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    leased_by: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_jobs_dispatch", "status", "run_at"),
        Index("ix_jobs_run", "run_id"),
        # at most one live job per (run,node): prevents duplicate execution
        Index(
            "uq_jobs_live",
            "run_id",
            "node_id",
            unique=True,
            postgresql_where=text("status IN ('queued','leased')"),
        ),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    started_at: Mapped[datetime] = _ts()
    heartbeat_at: Mapped[datetime] = _ts()
    info: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)


# ----------------------------------------------------------- observability ---
class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # SSE cursor
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str | None] = mapped_column(String(24))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    ts: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_events_run", "run_id", "id"), Index("ix_events_workspace_type", "workspace_id", "type"))


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)  # compile | node | verify | eval
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok | error
    error_class: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    cost_basis: Mapped[str] = mapped_column(String(16), nullable=False, server_default="UNAVAILABLE")
    request_hash: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(120))
    ts: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_model_calls_run", "run_id"), Index("ix_model_calls_ws_ts", "workspace_id", "ts"))


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID] = _fk("runs")
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)  # ALLOW | DENY
    decision_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | error | blocked
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_tool_calls_run", "run_id"),)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID] = _fk("runs")
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_checkpoints_run_node", "run_id", "node_id", "ts"),)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # small structured content
    storage_key: Mapped[str | None] = mapped_column(Text)  # large content lives in object storage
    content_type: Mapped[str] = mapped_column(String(80), nullable=False, server_default="application/json")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_artifacts_run", "run_id"), Index("ix_artifacts_project", "project_id", "type"))


class VerificationResultRow(Base):
    __tablename__ = "verification_results"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID] = _fk("runs")
    node_id: Mapped[str | None] = mapped_column(String(120))
    subject_artifact_id: Mapped[uuid.UUID] = _fk("artifacts")
    claim_ref: Mapped[str | None] = mapped_column(String(120))  # e.g. finding id inside the artifact
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # VERIFIED | REJECTED | UNCERTAIN
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    missing_evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    recommendations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_verification_run", "run_id"),)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    run_id: Mapped[uuid.UUID] = _fk("runs")
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="PENDING")  # PENDING|GRANTED|REJECTED
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decided_by: Mapped[uuid.UUID | None] = _fk("users", nullable=True, ondelete="SET NULL")
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_approvals_run", "run_id"),)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    scope: Mapped[str] = mapped_column(String(12), nullable=False)  # project | agent
    owner: Mapped[str] = mapped_column(String(80), nullable=False)  # agent id for scratch, 'project' otherwise
    run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True, ondelete="SET NULL")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_memory_project_scope", "project_id", "scope", "owner"),)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    task: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users", ondelete="RESTRICT")
    created_at: Mapped[datetime] = _ts()


class EvaluationArm(Base):
    __tablename__ = "evaluation_arms"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk("workspaces")
    evaluation_id: Mapped[uuid.UUID] = _fk("evaluations")
    mode: Mapped[str] = mapped_column(String(24), nullable=False)  # single_agent | manual_workflow | forge_workflow
    run_id: Mapped[uuid.UUID | None] = _fk("runs", nullable=True, ondelete="SET NULL")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("evaluation_id", "mode", name="uq_eval_arm_mode"),)


class RateLimitWindow(Base):
    __tablename__ = "rate_limit_windows"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Blob(Base):
    """Object storage inside Postgres (STORAGE_DRIVER=postgres) for deployments without an S3 bucket."""

    __tablename__ = "blobs"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, server_default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID | None] = _fk("workspaces", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_audit_workspace_ts", "workspace_id", "ts"),)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    run_logs_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    artifacts_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    memory_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="180")
    traces_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    updated_at: Mapped[datetime] = _ts()


