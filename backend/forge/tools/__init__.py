"""Tool implementations + the single validated entry point `execute_tool`.

Authorization (forge.policy) happens in the engine BEFORE this is called; execute_tool additionally
validates input against the tool's declared schema.
"""
from __future__ import annotations

from typing import Any

import jsonschema

from forge.registry.tools import TOOLS
from forge.tools.base import ToolContext, ToolError, ToolFn
from forge.tools.deps import dependency_manifest, osv_lookup
from forge.tools.patterns import pattern_scan
from forge.tools.repo import code_search, file_search, repository_read, repository_write
from forge.tools.testrunner import test_runner
from forge.tools.web import tavily_search


def _artifact_read(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    import uuid

    from forge.artifacts import load_content
    from forge.db import session_scope
    from forge.models import Artifact

    try:
        aid = uuid.UUID(args["artifact_id"])
    except ValueError as exc:
        raise ToolError("bad_id", "artifact_id must be a UUID.") from exc
    with session_scope() as db:
        a = db.get(Artifact, aid)
        # scoped to the run's workspace AND project: a guessed id from elsewhere is simply "not found"
        if a is None or a.workspace_id != ctx.workspace_id or a.project_id != ctx.project_id or a.deleted_at:
            raise ToolError("not_found", "Artifact not found.")
        content = load_content(ctx.store, a)
        return {"id": str(a.id), "type": a.type, "schema": a.schema_name, "content": content, "hash": a.content_hash}


def _artifact_write(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from forge.artifacts import create_artifact
    from forge.db import session_scope

    with session_scope() as db:
        a = create_artifact(
            db, ctx.store, workspace_id=ctx.workspace_id, project_id=ctx.project_id, run_id=ctx.run_id, node_id=ctx.node_id,
            type=str(args["type"])[:48], schema_name=str(args["schema"])[:80], content=args["content"],
            provenance={"producer": ctx.agent_id, "run_id": str(ctx.run_id), "node_id": ctx.node_id, "via": "ArtifactWrite"},
        )
        return {"artifact_id": str(a.id), "hash": a.content_hash}


IMPLS: dict[str, ToolFn] = {
    "RepositoryRead": repository_read,
    "FileSearch": file_search,
    "CodeSearch": code_search,
    "PatternScan": pattern_scan,
    "DependencyManifest": dependency_manifest,
    "OsvLookup": osv_lookup,
    "RepositoryWrite": repository_write,
    "TestRunner": test_runner,
    "TavilySearch": tavily_search,
    "ArtifactRead": _artifact_read,
    "ArtifactWrite": _artifact_write,
}

assert set(IMPLS) == set(TOOLS), "every registered tool needs an implementation (and vice versa)"


def execute_tool(ctx: ToolContext, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS[tool_id]
    if not isinstance(args, dict):
        raise ToolError("bad_input", "Tool input must be a JSON object.")
    try:
        jsonschema.validate(args, tool.input_schema or {"type": "object"})
    except jsonschema.ValidationError as exc:
        raise ToolError("bad_input", f"Invalid input for {tool_id}: {exc.message}") from exc
    return IMPLS[tool_id](ctx, args)
