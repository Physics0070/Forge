"""Tool *definitions* (what a tool needs and what it can affect). Implementations: forge.tools.*"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SideEffect = Literal["none", "workspace_write", "sandbox_exec", "external_network"]

PERMISSIONS: dict[str, str] = {
    "repository.read": "Read files of the run's repository snapshot",
    "repository.write": "Modify files in the isolated run workspace (never upstream)",
    "search.web": "Query external web search (Tavily)",
    "artifact.read": "Read artifacts of the current project",
    "artifact.write": "Create artifacts for the current run",
    "tests.run": "Execute tests inside the sandbox",
    "memory.read": "Read project memory",
    "memory.write": "Write project memory",
}


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    permissions: tuple[str, ...]  # ALL must be granted to the calling node
    side_effects: SideEffect = "none"
    requires_approval: bool = False  # a human APPROVAL must precede any use
    input_schema: dict[str, Any] = field(default_factory=dict, hash=False)
    output_schema: dict[str, Any] = field(default_factory=dict, hash=False)


_OBJ = {"type": "object"}

TOOLS: dict[str, ToolDefinition] = {
    t.id: t
    for t in [
        ToolDefinition(
            "RepositoryRead", "Repository Read", "Read a file (or list a directory) in the repository snapshot.",
            ("repository.read",),
            input_schema={"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                          "end_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False},
        ),
        ToolDefinition(
            "FileSearch", "File Search", "Find files by glob pattern.", ("repository.read",),
            input_schema={"type": "object", "properties": {"glob": {"type": "string"}}, "required": ["glob"],
                          "additionalProperties": False},
        ),
        ToolDefinition(
            "CodeSearch", "Code Search", "Regex search across repository files.", ("repository.read",),
            input_schema={"type": "object", "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"},
                          "max_results": {"type": "integer"}}, "required": ["pattern"], "additionalProperties": False},
        ),
        ToolDefinition(
            "RepositoryWrite", "Repository Write", "Apply a patch inside the isolated run workspace.",
            ("repository.write",), side_effects="workspace_write", requires_approval=True,
            input_schema={"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"],
                          "additionalProperties": False},
        ),
        ToolDefinition(
            "TestRunner", "Test Runner", "Run the project's tests inside an isolated sandbox without network.",
            ("tests.run", "repository.read"), side_effects="sandbox_exec",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}, "timeout_s": {"type": "integer"}},
                          "required": ["command"], "additionalProperties": False},
        ),
        ToolDefinition(
            "TavilySearch", "Tavily Search", "Web research; results are persisted as EvidenceArtifacts.",
            ("search.web",), side_effects="external_network",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                          "required": ["query"], "additionalProperties": False},
        ),
        ToolDefinition(
            "ArtifactRead", "Artifact Read", "Read a stored artifact of this project by id.", ("artifact.read",),
            input_schema={"type": "object", "properties": {"artifact_id": {"type": "string"}},
                          "required": ["artifact_id"], "additionalProperties": False},
        ),
        ToolDefinition(
            "ArtifactWrite", "Artifact Write", "Persist a typed artifact for this run.", ("artifact.write",),
            input_schema={"type": "object", "properties": {"type": {"type": "string"}, "schema": {"type": "string"},
                          "content": _OBJ}, "required": ["type", "schema", "content"], "additionalProperties": False},
        ),
    ]
}
