"""Shared runtime types for node execution."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.config import Settings, get_settings
from forge.providers.base import ModelProvider
from forge.providers.nebius import NebiusProvider
from forge.providers.pricing import load_pricing
from forge.registry.agents import AgentDefinition
from forge.workflow.ir import Workflow, WorkflowNode
from forge.workflow.retry import ErrorClass, NodeError  # re-exported
from forge.workflow.router import RouterConfig

__all__ = ["NodeError", "ErrorClass"]


# ---- control-flow signals (not errors) ---------------------------------------
class NodeSignal(Exception):
    pass


class NodePaused(NodeSignal):
    pass


class NodeSuspended(NodePaused):
    """The worker's time slice is ending (serverless): checkpoint and hand the node back to the queue."""


class NodeCancelled(NodeSignal):
    pass


class LeaseLost(NodeSignal):
    pass


class NodeBlocked(NodeSignal):
    def __init__(self, reason: str, decision: dict[str, Any]):
        super().__init__(reason)
        self.reason, self.decision = reason, decision


class NodeWaiting(NodeSignal):
    def __init__(self, kind: str, request: dict[str, Any]):
        super().__init__(kind)
        self.kind, self.request = kind, request


class NodeLoopback(NodeSignal):
    def __init__(self, target: str, feedback: dict[str, Any]):
        super().__init__(target)
        self.target, self.feedback = target, feedback


# ---- provider / router wiring --------------------------------------------------
_provider_lock = threading.Lock()
_provider: ModelProvider | None = None


def get_provider() -> ModelProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            s = get_settings()
            _provider = NebiusProvider(s.nebius_api_key, s.nebius_base_url, max_concurrency=s.nebius_max_concurrency)
        return _provider


def set_provider(p: ModelProvider | None) -> None:
    """Dependency-injection seam. Production never calls this; tests inject scripted providers."""
    global _provider
    with _provider_lock:
        _provider = p


def router_config(settings: Settings | None = None) -> RouterConfig:
    s = settings or get_settings()
    models = {t: s.model_for_tier(t) for t in ("nano", "super", "ultra")}
    if not any(models.values()) and s.nebius_model_default:
        models["super"] = s.nebius_model_default  # single-model deployment
    pricing = load_pricing()
    tier_cost: dict[str, float | None] = {}
    for t, m in models.items():
        pr = pricing.get(m) if m else None
        tier_cost[t] = ((pr.input_per_mtok + pr.output_per_mtok) / 2) if pr else None
    return RouterConfig(models={k: v for k, v in models.items() if v}, tier_cost_per_mtok=tier_cost)


@dataclass
class ArtifactSpec:
    type: str
    schema_name: str
    content: Any
    content_type: str = "application/json"
    provenance_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    output: dict[str, Any]
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    model: str | None = None
    routing: dict[str, Any] | None = None
    assign_failed: dict[str, dict[str, Any]] = field(default_factory=dict)  # RECOVERY: node_id -> repaired output
    flagged: bool = False  # verification policy "continue_flagged"
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeCtx:
    settings: Settings
    provider: ModelProvider
    worker_id: str
    job_id: uuid.UUID
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    node: WorkflowNode
    wf: Workflow
    agent: AgentDefinition | None
    input: dict[str, Any]
    attempt: int
    iteration: int
    feedback: dict[str, Any] | None
    model_override: str | None
    run_input: dict[str, Any]
    replay_model: str | None
    repo_dir: Path | None
    write_approved: bool
    started_monotonic: float = field(default_factory=time.monotonic)
    yield_at: float | None = None  # monotonic time after which the node must suspend (serverless slices)

    def should_yield(self) -> bool:
        return self.yield_at is not None and time.monotonic() >= self.yield_at

    @property
    def deadline(self) -> float:
        return self.started_monotonic + self.node.timeout_s

    def remaining_s(self) -> float:
        return self.deadline - time.monotonic()
