"""Pure scheduling logic: given node states + outputs, what should happen to a PENDING node?"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from forge.workflow.ir import NodeType, Workflow, WorkflowEdge, WorkflowNode
from forge.workflow.state import NodeState as N

Action = Literal["READY", "SKIP", "BLOCK", "WAIT"]
EdgeStatus = Literal["live", "dead", "pending", "failed"]

_ACTIVE = {N.PENDING, N.READY, N.RUNNING, N.RETRYING, N.WAITING_APPROVAL, N.VERIFICATION_FAILED, N.RECOVERING}


@dataclass(frozen=True)
class NodeView:
    state: N
    blocked_reason: str | None = None
    output: dict[str, Any] | None = None


def _is_failed(v: NodeView) -> bool:
    return v.state in (N.FAILED, N.CANCELLED) or (
        v.state == N.BLOCKED and v.blocked_reason in ("upstream_failed", "approval_rejected"))


def recovery_pending_nodes(wf: Workflow, views: dict[str, NodeView]) -> set[str]:
    """Nodes whose failure is still going to be handled by a RECOVERY node that has not finished."""
    out: set[str] = set()
    for n in wf.nodes:
        if n.type == NodeType.RECOVERY and views[n.id].state in (N.PENDING, N.READY, N.RUNNING, N.RECOVERING):
            out.update(n.config.get("watches") or [])
    return out


def edge_status(e: WorkflowEdge, src: WorkflowNode, v: NodeView, recovery_pending: set[str]) -> EdgeStatus:
    if e.kind == "failure":
        if v.state == N.FAILED:
            return "live"
        if v.state in (N.SUCCESS, N.SKIPPED):
            return "dead"
        return "failed" if _is_failed(v) else "pending"
    if v.state == N.SKIPPED:
        return "dead"
    if v.state == N.SUCCESS:
        if src.type == NodeType.CONDITION and e.condition:
            label = str(bool((v.output or {}).get("result"))).lower()
            return "live" if label == e.condition else "dead"
        return "live"
    if _is_failed(v):
        return "pending" if e.source in recovery_pending else "failed"
    return "pending"  # PENDING/READY/RUNNING/RETRYING/WAITING_APPROVAL/RECOVERING/budget-BLOCKED...


def evaluate(wf: Workflow, node: WorkflowNode, views: dict[str, NodeView]) -> tuple[Action, str | None]:
    rp = recovery_pending_nodes(wf, views)

    if node.type == NodeType.RECOVERY:
        watched = node.config.get("watches") or []
        vs = [views[w] for w in watched if w in views]
        if any(v.state in _ACTIVE for v in vs):
            return "WAIT", None
        return ("READY", None) if any(v.state == N.FAILED for v in vs) else ("SKIP", "nothing to recover")

    incoming = wf.incoming(node.id)
    if not incoming:
        return "READY", None
    nodes = {n.id: n for n in wf.nodes}
    st = {e.id: edge_status(e, nodes[e.source], views[e.source], rp) for e in incoming}

    if node.type == NodeType.JOIN:
        required = node.config.get("required") or [e.source for e in incoming]
        mode = node.config.get("mode", "all")
        statuses: list[EdgeStatus] = []
        for r in required:
            direct = [e for e in incoming if e.source == r]
            if direct:
                statuses.append(st[direct[0].id])
            else:  # required branch reached via an intermediate node: judge by its own state
                v = views[r]
                statuses.append("live" if v.state == N.SUCCESS else "dead" if v.state == N.SKIPPED
                                else "failed" if _is_failed(v) and r not in rp else "pending")
        if mode == "any":
            if "live" in statuses:
                return "READY", None
            if all(s == "dead" for s in statuses):
                return "SKIP", "no upstream branch ran"
            if "pending" in statuses:
                return "WAIT", None
            return "BLOCK", "upstream_failed"
        if all(s == "dead" for s in statuses):
            return "SKIP", "no upstream branch ran"
        if "failed" in statuses:
            return "BLOCK", "upstream_failed"
        if "pending" in statuses:
            return "WAIT", None
        return "READY", None

    vals = list(st.values())
    if all(s == "dead" for s in vals):
        return "SKIP", "all incoming branches skipped"
    if "failed" in vals:
        return "BLOCK", "upstream_failed"
    if "pending" in vals:
        return "WAIT", None
    return "READY", None


def run_outcome(wf: Workflow, views: dict[str, NodeView]) -> Literal["ACTIVE", "WAITING_APPROVAL", "BLOCKED", "SUCCESS", "FAILED"]:
    states = [v.state for v in views.values()]
    if any(s in (N.READY, N.RUNNING, N.RETRYING, N.RECOVERING, N.VERIFICATION_FAILED) for s in states):
        return "ACTIVE"
    if any(s == N.PENDING for s in states):
        # pending nodes with nothing active ahead of them are only waiting on approvals/blocks
        pass
    if any(s == N.WAITING_APPROVAL for s in states):
        return "WAITING_APPROVAL"
    if any(v.state == N.BLOCKED and v.blocked_reason == "budget" for v in views.values()):
        return "BLOCKED"
    if any(s in (N.FAILED, N.CANCELLED) for s in states) or any(
            v.state == N.BLOCKED for v in views.values()):
        return "FAILED"
    if any(s == N.PENDING for s in states):
        return "ACTIVE"  # transient; the scheduler will resolve it
    return "SUCCESS"
