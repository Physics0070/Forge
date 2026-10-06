"""Canonical, serializable Workflow IR.

Runtime semantics per node type (implemented by forge.engine, validated by forge.workflow.validate):

AGENT         run an agent (model + tools) -> output validated against outputSchema
PARALLEL      fan-out barrier: succeeds immediately; ALL outgoing edges activate concurrently
JOIN          waits for the branches in config.required (default: all upstream); merges their outputs
              keyed by source node id. config.mode = "all" | "any"
CONDITION     deterministic predicate over upstream output; activates only edges whose `condition`
              equals the result ("true"/"false"); other branches become SKIPPED
RETRY         bounded loop-back: checks `config.until` against `config.target`'s output; on failure
              re-runs the target with feedback, up to config.max_iterations
APPROVAL      pauses the run (WAITING_APPROVAL) until a human grants/rejects; passes input through
VERIFICATION  independent verification of upstream claims/artifacts (forge.verification)
RECOVERY      armed by config.watches; runs only if a watched node FAILED, else SKIPPED
TRANSFORM     deterministic reshaping of data (no model call): config.select / config.set
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IR_VERSION = "1.0"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")


class NodeType(str, Enum):
    AGENT = "AGENT"
    PARALLEL = "PARALLEL"
    JOIN = "JOIN"
    CONDITION = "CONDITION"
    RETRY = "RETRY"
    APPROVAL = "APPROVAL"
    VERIFICATION = "VERIFICATION"
    RECOVERY = "RECOVERY"
    TRANSFORM = "TRANSFORM"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryPolicy(_Strict):
    max_attempts: int = Field(2, ge=1, le=6, alias="maxAttempts")
    backoff: Literal["none", "fixed", "exponential"] = "exponential"
    base_delay_s: float = Field(1.0, ge=0, le=60, alias="baseDelayS")
    max_delay_s: float = Field(30.0, ge=0, le=600, alias="maxDelayS")
    jitter: float = Field(0.25, ge=0, le=1)
    # error classes that may be retried (see forge.workflow.retry.ErrorClass)
    retryable_errors: list[str] = Field(
        default_factory=lambda: ["transient", "timeout", "rate_limited", "model_failure"], alias="retryableErrors"
    )
    validation_retries: int = Field(1, ge=0, le=3, alias="validationRetries")
    fallback_model: str | None = Field(None, alias="fallbackModel")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NodeBudget(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    max_usd: float | None = Field(None, ge=0, alias="maxUsd")
    max_tokens: int | None = Field(None, ge=1, alias="maxTokens")


class VerificationPolicy(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    required: bool = False
    min_confidence: float = Field(0.6, ge=0, le=1, alias="minConfidence")
    min_evidence_score: float = Field(0.5, ge=0, le=1, alias="minEvidenceScore")
    on_fail: Literal["fail", "recover", "continue_flagged"] = Field("fail", alias="onFail")


class Routing(_Strict):
    """Per-node routing hints. `model` (explicit) always wins over the router."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    complexity: Literal["low", "medium", "high"] = "medium"
    risk: Literal["low", "medium", "high"] = "low"
    latency: Literal["fast", "normal", "relaxed"] = "normal"
    verification_critical: bool = Field(False, alias="verificationCritical")


class WorkflowNode(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    type: NodeType
    name: str = ""
    agent_id: str | None = Field(None, alias="agentId")
    config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"}, alias="inputSchema")
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"}, alias="outputSchema")
    model: str | None = None  # explicit model id; None => router decides at run time
    routing: Routing = Field(default_factory=Routing)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy, alias="retryPolicy")
    timeout_s: int = Field(120, ge=1, le=3600, alias="timeoutS")
    budget: NodeBudget = Field(default_factory=NodeBudget)
    verification_policy: VerificationPolicy = Field(default_factory=VerificationPolicy, alias="verificationPolicy")
    locked: bool = False  # locked nodes are not modified by the compiler / re-compilation

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("node id must match [A-Za-z][A-Za-z0-9_-]{0,63}")
        return v


class FieldMapping(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source: str = Field(alias="from")  # "$.path.in.source.output" | "$workflow.path" | "$const"
    target: str = Field(alias="to")  # top-level or dotted key in target input
    const: Any = None  # used when source == "$const"


class WorkflowEdge(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    source: str
    target: str
    kind: Literal["data", "control", "failure"] = "data"
    condition: str | None = None  # CONDITION label: "true" | "false"
    mapping: list[FieldMapping] = Field(default_factory=list)
    # If mapping is empty the whole source output is passed as the target's input (still schema-validated).
    on_handoff_failure: Literal["fail", "retry_source", "recover"] = Field("retry_source", alias="onHandoffFailure")


class BudgetPolicy(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    max_usd: float | None = Field(None, ge=0, alias="maxUsd")
    max_tokens: int | None = Field(None, ge=1, alias="maxTokens")
    # when a model has no configured price, USD can't be computed: enforce tokens only, or block outright
    unpriced_behavior: Literal["enforce_tokens_only", "block"] = Field("enforce_tokens_only", alias="unpricedBehavior")
    on_exceed: Literal["block"] = Field("block", alias="onExceed")


class MemoryPolicy(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_memory: Literal["off", "read", "read_write"] = Field("read", alias="projectMemory")
    scratch_ttl_hours: int = Field(24, ge=1, le=720, alias="scratchTtlHours")


class WorkflowPolicies(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    # Capabilities this workflow may *ever* use. Nodes can only narrow, never widen.
    allowed_permissions: list[str] = Field(
        default_factory=lambda: ["repository.read", "search.web", "artifact.read", "artifact.write"],
        alias="allowedPermissions",
    )
    require_approval_for_writes: bool = Field(True, alias="requireApprovalForWrites")
    max_parallelism: int = Field(4, ge=1, le=32, alias="maxParallelism")


class Workflow(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ir_version: str = Field(IR_VERSION, alias="irVersion")
    id: str | None = None
    project_id: str | None = Field(None, alias="projectId")
    version: int | None = None
    name: str
    description: str = ""
    goal: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge] = Field(default_factory=list)
    policies: WorkflowPolicies = Field(default_factory=WorkflowPolicies)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy, alias="memoryPolicy")
    budget_policy: BudgetPolicy = Field(default_factory=BudgetPolicy, alias="budgetPolicy")
    verification_policy: VerificationPolicy = Field(default_factory=VerificationPolicy, alias="verificationPolicy")
    # Which node outputs form the run's final result (defaults to sink nodes).
    outputs: list[str] = Field(default_factory=list)
    created_by: str | None = Field(None, alias="createdBy")
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    def node(self, node_id: str) -> WorkflowNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def incoming(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.target == node_id]

    def outgoing(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def to_json(self) -> dict[str, Any]:
        return json.loads(self.model_dump_json(by_alias=True, exclude_none=True))

    def content_hash(self) -> str:
        """Hash over the executable content only (not ids/timestamps), for reproducibility checks."""
        body = self.to_json()
        for k in ("id", "projectId", "version", "createdBy", "createdAt", "updatedAt"):
            body.pop(k, None)
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Workflow":
        return cls.model_validate(data)
