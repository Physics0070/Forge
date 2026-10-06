"""Read-side aggregation: run totals, honest cost basis, comparison and evaluation metrics.

Rule: never fabricate. A metric with no underlying data is returned as None ("Not available").
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forge.models import (Approval, Artifact, Event, ModelCall, Run, RunNode, ToolCall, VerificationResultRow, Workflow, WorkflowVersion)
from forge.workflow.ir import Workflow as IR


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def run_totals(db: Session, run: Run) -> dict[str, Any]:
    calls = db.execute(select(ModelCall.cost_usd, ModelCall.cost_basis, ModelCall.input_tokens, ModelCall.output_tokens,
                              ModelCall.total_tokens, ModelCall.latency_ms, ModelCall.model, ModelCall.status,
                              ModelCall.error_class, ModelCall.purpose).where(ModelCall.run_id == run.id)).all()
    priced = [c for c in calls if c.cost_usd is not None]
    unpriced = len(calls) - len(priced)
    if not priced:
        basis = "UNAVAILABLE"
    else:
        basis = "ESTIMATED" if any(c.cost_basis == "ESTIMATED" for c in priced) else "ACTUAL"
    by_model: dict[str, dict[str, Any]] = {}
    for c in calls:
        m = by_model.setdefault(c.model, {"model": c.model, "calls": 0, "inputTokens": 0, "outputTokens": 0, "costUsd": None, "errors": 0})
        m["calls"] += 1
        m["inputTokens"] += c.input_tokens or 0
        m["outputTokens"] += c.output_tokens or 0
        m["errors"] += 1 if c.status == "error" else 0
        if c.cost_usd is not None:
            m["costUsd"] = round((m["costUsd"] or 0) + float(c.cost_usd), 6)
    lat = sorted(c.latency_ms for c in calls if c.latency_ms is not None)
    return {
        "modelCalls": len(calls), "failedModelCalls": sum(1 for c in calls if c.status == "error"),
        "inputTokens": sum(c.input_tokens or 0 for c in calls), "outputTokens": sum(c.output_tokens or 0 for c in calls),
        "totalTokens": sum(c.total_tokens or 0 for c in calls),
        "costUsd": round(sum(float(c.cost_usd) for c in priced), 6) if priced else None,
        "costBasis": basis, "costPartial": bool(priced) and unpriced > 0, "unpricedCalls": unpriced,
        "latencyMs": {"avg": round(sum(lat) / len(lat)) if lat else None, "p50": lat[len(lat) // 2] if lat else None,
                      "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
                      "sum": sum(lat) if lat else None},
        "byModel": list(by_model.values()),
    }


def duration_s(run: Run) -> float | None:
    end = run.completed_at
    if run.started_at and end:
        return round((end - run.started_at).total_seconds(), 3)
    return None


def node_out(rn: RunNode, wf: IR | None, *, detail: bool = False) -> dict[str, Any]:
    node = wf.node(rn.node_id) if wf else None
    out = {
        "id": rn.node_id, "type": node.type.value if node else None, "name": (node.name if node else "") or rn.node_id,
        "agentId": node.agent_id if node else None, "state": rn.state, "attempt": rn.attempt, "iteration": rn.iteration,
        "model": rn.model, "routing": rn.routing, "error": rn.error, "blockedReason": rn.blocked_reason,
        "startedAt": rn.started_at.isoformat() if rn.started_at else None,
        "finishedAt": rn.finished_at.isoformat() if rn.finished_at else None,
        "reusedFromRun": str(rn.reused_from_run) if rn.reused_from_run else None, "feedback": rn.feedback,
    }
    if detail:
        out["input"], out["output"] = rn.input, rn.output
    return out


def run_out(db: Session, run: Run, *, nodes: bool = True) -> dict[str, Any]:
    wv = db.get(WorkflowVersion, run.workflow_version_id)
    wfrow = db.get(Workflow, wv.workflow_id) if wv else None
    wf = IR.from_json(run.ir_snapshot)
    pend = db.execute(select(Approval).where(Approval.run_id == run.id, Approval.status == "PENDING")).scalars().all()
    totals = run_totals(db, run)
    retries = db.execute(select(func.count()).select_from(Event).where(Event.run_id == run.id, Event.type == "RETRY_STARTED")).scalar_one()
    violations = db.execute(select(func.count()).select_from(Event).where(Event.run_id == run.id, Event.type == "POLICY_BLOCKED")).scalar_one()
    out: dict[str, Any] = {
        "id": str(run.id), "projectId": str(run.project_id), "workflowId": str(wv.workflow_id) if wv else None,
        "workflowName": wfrow.name if wfrow else wf.name, "workflowVersionId": str(run.workflow_version_id),
        "version": wv.version if wv else None, "goal": wf.goal, "status": run.status, "input": run.input,
        "repositoryId": str(run.repository_id) if run.repository_id else None,
        "parentRunId": str(run.parent_run_id) if run.parent_run_id else None, "replayConfig": run.replay_config,
        "createdAt": run.created_at.isoformat(), "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None, "durationS": duration_s(run),
        "budget": {"maxUsd": _f(run.budget_usd), "maxTokens": run.budget_tokens, "spentUsd": float(run.spent_usd),
                   "spentTokens": int(run.spent_tokens), "reservedUsd": float(run.reserved_usd)},
        "totals": {**totals, "retries": retries, "policyViolations": violations},
        "error": run.error, "pendingApprovals": [approval_out(a) for a in pend],
        "pauseRequested": run.pause_requested, "cancelRequested": run.cancel_requested,
    }
    if nodes:
        rns = {rn.node_id: rn for rn in db.execute(select(RunNode).where(RunNode.run_id == run.id)).scalars()}
        order = [n.id for n in wf.nodes]
        out["nodes"] = [node_out(rns[i], wf) for i in order if i in rns]
        out["graph"] = {"nodes": [{"id": n.id, "type": n.type.value, "name": n.name or n.id, "agentId": n.agent_id} for n in wf.nodes],
                        "edges": [{"id": e.id, "source": e.source, "target": e.target, "kind": e.kind, "condition": e.condition}
                                  for e in wf.edges]}
    return out


def approval_out(a: Approval) -> dict[str, Any]:
    return {"id": str(a.id), "runId": str(a.run_id), "nodeId": a.node_id, "kind": a.kind, "status": a.status, "request": a.request,
            "createdAt": a.created_at.isoformat(), "decidedAt": a.decided_at.isoformat() if a.decided_at else None,
            "note": a.decision_note}


def event_out(e: Event) -> dict[str, Any]:
    return {"id": e.id, "runId": str(e.run_id) if e.run_id else None, "nodeId": e.node_id, "type": e.type, "status": e.status,
            "ts": e.ts.isoformat(), "metadata": e.meta}


# ----------------------------------------------------------- metrics (eval/compare)
def run_metrics(db: Session, run: Run) -> dict[str, Any]:
    t = run_totals(db, run)
    rns = db.execute(select(RunNode).where(RunNode.run_id == run.id)).scalars().all()
    ev = dict(db.execute(select(Event.type, func.count()).where(Event.run_id == run.id).group_by(Event.type)).all())
    failed_events, recovered = ev.get("NODE_FAILED", 0), ev.get("NODE_RECOVERED", 0)
    tool_rows = db.execute(select(ToolCall.tool, ToolCall.input).where(ToolCall.run_id == run.id, ToolCall.decision == "ALLOW")).all()
    sigs = [hashlib.sha256((r.tool + json.dumps(r.input, sort_keys=True, default=str)).encode()).hexdigest() for r in tool_rows]
    dup = len(sigs) - len(set(sigs))
    vr = db.execute(select(VerificationResultRow.status, func.count()).where(VerificationResultRow.run_id == run.id)
                    .group_by(VerificationResultRow.status)).all()
    vcount = {s: n for s, n in vr}
    vtotal = sum(vcount.values())
    calls = t["modelCalls"]
    invalid = db.execute(select(func.count()).select_from(ModelCall).where(ModelCall.run_id == run.id, ModelCall.error_class == "invalid_output")).scalar_one()
    nodes_ok = sum(1 for n in rns if n.state == "SUCCESS")
    return {
        "runId": str(run.id), "status": run.status, "success": run.status == "SUCCESS" if run.status in ("SUCCESS", "FAILED", "CANCELLED") else None,
        "durationS": duration_s(run), "totalTokens": t["totalTokens"], "costUsd": t["costUsd"], "costBasis": t["costBasis"],
        "modelCalls": calls, "retries": ev.get("RETRY_STARTED", 0), "failures": failed_events,
        "policyViolations": ev.get("POLICY_BLOCKED", 0),
        "schemaValidity": round(1 - invalid / calls, 3) if calls else None,
        "verificationRate": round(vcount.get("VERIFIED", 0) / vtotal, 3) if vtotal else None,
        "verification": {"verified": vcount.get("VERIFIED", 0), "rejected": vcount.get("REJECTED", 0), "uncertain": vcount.get("UNCERTAIN", 0)},
        "recoveryRate": round(recovered / failed_events, 3) if failed_events else None,
        "duplicateWork": {"duplicateToolCalls": dup, "totalToolCalls": len(sigs)} if sigs else None,
        "artifacts": db.execute(select(func.count()).select_from(Artifact).where(Artifact.run_id == run.id)).scalar_one(),
        "nodesSucceeded": nodes_ok, "nodesTotal": len(rns),
    }


def new_idempotency_key() -> str:
    return uuid.uuid4().hex
