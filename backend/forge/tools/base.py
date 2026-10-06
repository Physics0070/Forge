from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from forge.storage import ObjectStore

MAX_TOOL_OUTPUT_CHARS = 24_000


class ToolError(Exception):
    """A tool could not complete. `code` is machine-readable; message is safe to show the model/user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolContext:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    agent_id: str
    repo_dir: Path | None
    store: ObjectStore
    tavily_api_key: str = ""
    sandbox_enabled: bool = True
    sandbox_image: str = "python:3.12-slim"
    sandbox_network: bool = False
    http: httpx.Client | None = None
    # evidence/artifacts produced by tools are returned to the engine for persistence
    evidence: list[dict[str, Any]] = field(default_factory=list)
    applied_patches: list[str] = field(default_factory=list)
    memory_scope_owner: str = ""

    def client(self) -> httpx.Client:
        if self.http is None:
            self.http = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        return self.http

    def require_repo(self) -> Path:
        if self.repo_dir is None or not self.repo_dir.exists():
            raise ToolError("no_repository", "This run has no repository attached.")
        return self.repo_dir


ToolFn = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


def clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]", True
