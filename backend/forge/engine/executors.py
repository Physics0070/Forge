"""Runtime semantics of every non-AGENT node type + dispatcher."""
from __future__ import annotations

import logging
from typing import Any

import jsonschema
from sqlalchemy import delete, select

from forge.artifacts import create_artifact, load_content
from forge.db import session_scope
from forge.engine import agent_runner, llm
from forge.engine.core import clip_json, emit
from forge.engine.runtime import (ArtifactSpec, NodeBlocked, NodeCtx, NodeLoopback, NodeResult, NodeWaiting, router_config)
from forge.logging import log
from forge.models import Artifact, MemoryEntry, Run, RunNode, ToolCall, VerificationResultRow
from forge.providers.base import Message
from forge.registry.agents import BUILTIN_AGENTS
from forge.storage import get_store
from forge.tools.base import ToolContext
from forge.verification import verify_findings
from forge.workflow import paths
from forge.workflow.ir import NodeType, Routing
from forge.workflow.retry import ErrorClass, NodeError
from forge.workflow.router import route


class VerificationFailed(Exception):
    """Raised after a VERIFICATION node's policy rejects the outcome (node -> VERIFICATION_FAILED)."""

    def __init__(self, output: dict[str, Any], on_fail: str):
        super().__init__("verification failed")
        self.output, self.on_fail = output, on_fail


def execute(ctx: NodeCtx) -> NodeResult:
    t = ctx.node.type
    if t == NodeType.AGENT:
        res = agent_runner.run_agent(ctx)
        res = _authoritative_diff(ctx, res)
        return _post_agent_verification(ctx, res)
    if t == NodeType.PARALLEL:
        # fan-out barrier: passes its input through unchanged and lists the branches it activates
        return NodeResult(output={**ctx.input, "branches": [e.target for e in ctx.wf.outgoing(ctx.node.id)]})
    if t == NodeType.JOIN:
        return _join(ctx)
    if t == NodeType.CONDITION:
        return _condition(ctx)
    if t == NodeType.TRANSFORM:
        return NodeResult(output=paths.apply_transform(ctx.node.config, ctx.input))
    if t == NodeType.RETRY:
        return _retry(ctx)
    if t == NodeType.APPROVAL:
        return _approval(ctx)
    if t == NodeType.VERIFICATION:
        return _verification(ctx)
    if t == NodeType.RECOVERY:
        return _recovery(ctx)
    raise NodeError(ErrorClass.PERMANENT, f"Unsupported node type {t}")


def _authoritative_diff(ctx: NodeCtx, res: NodeResult) -> NodeResult:
    """An agent's claim about what it changed is never trusted: the diff is recomputed from the isolated workspace."""
    if "RepositoryWrite" not in ctx.node.tools or ctx.repo_dir is None:
        return res
    from forge.tools.repo import current_diff

    real = current_diff(ctx.repo_dir)
    if "diff" in (ctx.node.output_schema.get("properties") or {}):
        res.output = {**res.output, "diff": real}
    res.artifacts = [a for a in res.artifacts if a.type != "patch"]
    if real.strip():
        res.artifacts.append(ArtifactSpec("patch", "UnifiedDiff", real, content_type="text/x-diff",
                                          provenance_extra={"computed_by": "forge (git diff of isolated workspace)"}))
    return res


# --------------------------------------------------------------- simple nodes
def _join(ctx: NodeCtx) -> NodeResult:
    out: dict[str, Any] = {}
    for target, spec in (ctx.node.config.get("concat") or {}).items():
        merged: list[Any] = []
        for p in spec:
            v = paths.get_path(ctx.input, p, None)
            if isinstance(v, list):
                merged.extend(v)
        out[target] = merged
    out["branches"] = ctx.input
    return NodeResult(output=out)


def _condition(ctx: NodeCtx) -> NodeResult:
    pred = ctx.node.config["predicate"]
    try:
        result = paths.eval_predicate(pred, ctx.input)
    except (ValueError, KeyError) as exc:
        raise NodeError(ErrorClass.PERMANENT, f"Invalid predicate: {exc}") from exc
    return NodeResult(output={"result": bool(result), "predicate": pred})


def _retry(ctx: NodeCtx) -> NodeResult:
    cfg = ctx.node.config
    target_out = ctx.input
    # input of a RETRY node is the passthrough of its target's output
    ok = paths.eval_predicate(cfg["until"], target_out)
    if ok:
        return NodeResult(output=target_out)
    if ctx.iteration >= int(cfg.get("max_iterations", 2)):
        raise NodeError(ErrorClass.VALIDATION_FAILURE,
                        f"Condition not met after {ctx.iteration} re-runs of '{cfg['target']}'.",
                        detail={"until": cfg["until"]})
    raise NodeLoopback(cfg["target"], {"reason": "The RETRY gate's condition was not met.", "until": cfg["until"],
                                       "previous_output": clip_json(target_out, 8000)})


def _approval(ctx: NodeCtx) -> NodeResult:
    cfg = ctx.node.config
    raise NodeWaiting(cfg.get("kind", "approve_action"), {
        "title": cfg.get("title", ctx.node.name or ctx.node.id), "summary": cfg.get("summary", ""),
        "kind": cfg.get("kind", "approve_action"), "payload": clip_json(ctx.input, 100_000)})


# ------------------------------------------------------------- verification
def _run_evidence(run_id) -> list[dict[str, Any]]:
    with session_scope() as db:
        arts = db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.type == "evidence")).scalars().all()
        store = get_store()
        return [c for a in arts if isinstance((c := load_content(store, a)), dict)]


def _run_tool_calls(run_id) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(select(ToolCall).where(ToolCall.run_id == run_id, ToolCall.decision == "ALLOW")).scalars().all()
        return [{"tool": r.tool, "input": r.input, "output_summary": r.output_summary, "node_id": r.node_id} for r in rows]


def _producer_models(run_id) -> set[str]:
    with session_scope() as db:
        return {m for (m,) in db.execute(select(RunNode.model).where(RunNode.run_id == run_id, RunNode.model.is_not(None)))}


def make_judge(ctx: NodeCtx):
    """Independent reviewer: prefers a model different from those that produced the claims."""
    agent = BUILTIN_AGENTS["verifier"]
    cfg = router_config(ctx.settings)
    try:
        decision = route(Routing(complexity="high", risk="high", verificationCritical=True), cfg)
    except Exception:
        return None
    model = decision.model
    producers = _producer_models(ctx.run_id)
    independent = model not in producers
    if not independent and decision.fallback_model:
        model, independent = decision.fallback_model, decision.fallback_model not in producers
    state = {"independent": independent, "blocked": False}

    def judge(claim: dict[str, Any], code_ctx: str) -> dict[str, Any] | None:
        if state["blocked"] or ctx.should_yield():
            return None
        msgs = [
            Message("system", agent.system_contract + "\n\nReply with ONE JSON object: "
                    '{"supports_claim": bool, "confidence": 0..1, "reason": str}.'),
            Message("user", "CLAIM (untrusted agent output):\n" + agent_runner.fence("claim", {
                k: claim.get(k) for k in ("title", "category", "file", "line", "description", "severity")}) +
                    "\n\nCODE AT THE CITED LOCATION (fetched by the system):\n" + agent_runner.fence("repo-code", code_ctx)),
        ]
        try:
            res = llm.call_structured(ctx, purpose="verify", model=model, messages=msgs, schema=agent.output_schema,
                                      schema_name="forge_verifier", max_tokens=600, temperature=0.0)
            return res.parsed
        except NodeBlocked:
            state["blocked"] = True
            return None
        except NodeError as exc:
            log("judge_failed", logging.WARNING, run_id=str(ctx.run_id), kind=exc.error_class.value)
            return None

    judge.independent = state  # type: ignore[attr-defined]
    return judge


def _verify(ctx: NodeCtx, findings: list[dict[str, Any]], extra_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = _run_evidence(ctx.run_id) + [e for e in extra_evidence if isinstance(e, dict)]
    tctx = ToolContext(ctx.workspace_id, ctx.project_id, ctx.run_id, ctx.node.id, "verifier", ctx.repo_dir, get_store())
    return verify_findings(findings, repo_dir=ctx.repo_dir, evidence=evidence, tool_calls=_run_tool_calls(ctx.run_id),
                           policy=ctx.node.verification_policy, judge=make_judge(ctx), ctx=tctx)


def _persist_verification(ctx: NodeCtx, findings: list[dict[str, Any]], out: dict[str, Any]) -> None:
    with session_scope() as db:
        run = db.get(Run, ctx.run_id)
        db.execute(delete(VerificationResultRow).where(VerificationResultRow.run_id == ctx.run_id,
                                                       VerificationResultRow.node_id == ctx.node.id))
        subject = create_artifact(
            db, get_store(), workspace_id=ctx.workspace_id, project_id=ctx.project_id, run_id=ctx.run_id, node_id=ctx.node.id,
            type="security_findings", schema_name="SecurityFindingList", content={"findings": findings},
            provenance={"producer": "verification", "stage": "verification_input", "run_id": str(ctx.run_id),
                        "node_id": ctx.node.id})
        for r in out["results"]:
            db.add(VerificationResultRow(
                workspace_id=ctx.workspace_id, run_id=ctx.run_id, node_id=ctx.node.id, subject_artifact_id=subject.id,
                claim_ref=r["finding_id"], status=r["status"], confidence=r["confidence"], evidence_score=r["evidenceScore"],
                reason=r["reason"], missing_evidence=r["missingEvidence"], recommendations=r["recommendations"],
                checks=r.get("checks", {})))
        s = out["summary"]
        emit(db, run, "VERIFICATION_PASSED" if s["verified"] else "VERIFICATION_FAILED", node_id=ctx.node.id,
             status="VERIFIED" if s["verified"] else "NONE_VERIFIED", **s)
        if ctx.wf.memory_policy.project_memory == "read_write":
            for f in out["verified_findings"]:
                db.add(MemoryEntry(
                    workspace_id=ctx.workspace_id, project_id=ctx.project_id, scope="project", owner="project",
                    run_id=ctx.run_id, source="verification",
                    content={"fact": f"VERIFIED {f['severity']}: {f['title']} ({f['file']}:{f.get('line')})",
                             "finding_id": f["id"]},
                    provenance={"run_id": str(ctx.run_id), "node_id": ctx.node.id, "verified_by": "forge.verification"}))


def _verification(ctx: NodeCtx) -> NodeResult:
    findings = paths.get_path(ctx.input, ctx.node.config.get("findings_path", "$.findings"), [])
    if not isinstance(findings, list):
        raise NodeError(ErrorClass.VALIDATION_FAILURE, "VERIFICATION input has no findings list.")
    with session_scope() as db:
        emit(db, db.get(Run, ctx.run_id), "VERIFICATION_STARTED", node_id=ctx.node.id, status="RUNNING", count=len(findings))
    out = _verify(ctx, findings, ctx.input.get("evidence") or [])
    _persist_verification(ctx, findings, out)
    return _apply_verification_policy(ctx, out, [ArtifactSpec("verification", "VerificationOutput", out)])


def _apply_verification_policy(ctx: NodeCtx, out: dict[str, Any], artifacts: list[ArtifactSpec]) -> NodeResult:
    vp = ctx.node.verification_policy
    s = out["summary"]
    if vp.required and s["total"] > 0 and s["verified"] == 0:
        if vp.on_fail == "continue_flagged":
            return NodeResult(output=out, artifacts=artifacts, flagged=True)
        raise VerificationFailed(out, vp.on_fail)
    return NodeResult(output=out, artifacts=artifacts)


def _post_agent_verification(ctx: NodeCtx, res: NodeResult) -> NodeResult:
    """AGENT nodes with verificationPolicy.required get their findings independently verified inline."""
    vp = ctx.node.verification_policy
    findings = res.output.get("findings")
    if not vp.required or not isinstance(findings, list):
        return res
    out = _verify(ctx, findings, res.output.get("evidence") or [])
    _persist_verification(ctx, findings, out)
    res.output = {**res.output, "verification": out["summary"],
                  "findings": [dict(f, state=next((r["status"] for r in out["results"] if r["finding_id"] == f.get("id")), "UNVERIFIED"))
                               for f in findings]}
    res.artifacts.append(ArtifactSpec("verification", "VerificationOutput", out))
    checked = _apply_verification_policy(ctx, out, res.artifacts)
    res.flagged = checked.flagged
    return res


# ---------------------------------------------------------------- recovery
def _recovery(ctx: NodeCtx) -> NodeResult:
    cfg = ctx.node.config
    failed: dict[str, Any] = ctx.input.get("failed") or {}
    if not failed:
        return NodeResult(output={"recovered": [], "mode": "noop"})
    if ctx.node.agent_id:
        res = agent_runner.run_agent(ctx)
        assign = {nid: res.output for nid in failed}
        return NodeResult(output={"recovered": list(failed), "mode": "agent"}, model=res.model, routing=res.routing,
                          artifacts=res.artifacts, assign_failed=assign)
    per_node = cfg.get("fallback_outputs") or {}
    assign: dict[str, dict[str, Any]] = {}
    for nid in failed:
        fb = per_node.get(nid, cfg.get("fallback_output"))
        if not isinstance(fb, dict):
            raise NodeError(ErrorClass.PERMANENT, f"RECOVERY has no fallback output for '{nid}'.")
        target = ctx.wf.node(nid)
        if target:
            try:
                jsonschema.validate(fb, target.output_schema)
            except jsonschema.ValidationError as exc:
                raise NodeError(ErrorClass.VALIDATION_FAILURE,
                                f"Fallback output for '{nid}' violates its schema: {exc.message[:200]}") from exc
        assign[nid] = fb
    return NodeResult(output={"recovered": list(failed), "mode": "static_fallback"}, assign_failed=assign)

