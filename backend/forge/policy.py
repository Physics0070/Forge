"""Capability / policy engine. Evaluated before EVERY tool invocation; denial means the call never runs."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from forge.registry.tools import TOOLS
from forge.workflow.ir import WorkflowNode, WorkflowPolicies


@dataclass(frozen=True)
class Decision:
    allow: bool
    operation: str  # the permission(s) requested, e.g. "repository.write"
    reason: str = ""
    policy: str = ""  # human-readable policy name, e.g. "READ ONLY"


def _policy_label(perms: tuple[str, ...]) -> str:
    return "READ ONLY" if any(p == "repository.write" for p in perms) else "CAPABILITY NOT GRANTED"


def authorize(
    *,
    agent_id: str,
    node: WorkflowNode,
    workflow_policies: WorkflowPolicies,
    tool_id: str,
    agent_permissions: tuple[str, ...] | None,
    actor_workspace_id: uuid.UUID,
    resource_workspace_id: uuid.UUID,
    write_approved: bool,
) -> Decision:
    tool = TOOLS.get(tool_id)
    if tool is None:
        return Decision(False, tool_id, f"Unknown tool '{tool_id}'.", "UNKNOWN TOOL")
    op = ",".join(tool.permissions)

    if actor_workspace_id != resource_workspace_id:
        return Decision(False, op, "Cross-workspace access is never allowed.", "WORKSPACE ISOLATION")
    if tool_id not in node.tools:
        return Decision(False, op, f"Tool '{tool_id}' is not granted to this node.", _policy_label(tool.permissions))
    missing = [p for p in tool.permissions if p not in node.permissions]
    if missing:
        return Decision(False, op, f"Node lacks permission(s): {', '.join(missing)}.", _policy_label(tool.permissions))
    outside = [p for p in tool.permissions if p not in workflow_policies.allowed_permissions]
    if outside:
        return Decision(False, op, f"Workflow policy does not allow: {', '.join(outside)}.", "WORKFLOW POLICY")
    if agent_permissions is not None:
        beyond = [p for p in tool.permissions if p not in agent_permissions]
        if beyond:
            return Decision(False, op, f"Agent '{agent_id}' is not permitted to hold: {', '.join(beyond)}.", "AGENT CAPABILITY")
    if tool.requires_approval and workflow_policies.require_approval_for_writes and not write_approved:
        return Decision(False, op, "A human approval is required before this action.", "APPROVAL REQUIRED")
    return Decision(True, op, "allowed")


def blocked_banner(agent_name: str, d: Decision, tool_id: str) -> dict[str, Any]:
    return {"agent": agent_name, "attempted": d.operation, "tool": tool_id, "policy": d.policy, "reason": d.reason,
            "message": f"{agent_name} attempted {d.operation}. Policy: {d.policy}. No changes were made."}
