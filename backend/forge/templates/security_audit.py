"""Workflow template: Repository Security Analysis & Remediation.

This is ordinary Workflow IR data. The engine has no knowledge of "security audits": the same engine
executes any IR (see tests/test_engine.py which runs unrelated graphs).
"""
from __future__ import annotations

from typing import Any

from forge import schemas as S
from forge.registry.agents import BUILTIN_AGENTS
from forge.workflow.ir import Workflow

KEY = "repository-security-audit"


def _agent(node_id: str, agent_id: str, name: str, *, input_schema: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    a = BUILTIN_AGENTS[agent_id]
    n: dict[str, Any] = {
        "id": node_id, "type": "AGENT", "name": name, "agentId": agent_id,
        "inputSchema": input_schema or a.input_schema, "outputSchema": a.output_schema,
        "tools": list(a.tools), "permissions": list(a.permissions),
        "routing": {"complexity": a.complexity, "risk": a.risk, "latency": a.latency,
                    "verificationCritical": a.verification_critical},
        "timeoutS": a.timeout_s, "retryPolicy": a.retry_policy.model_dump(by_alias=True),
    }
    n.update(kw)
    return n


def _edge(src: str, dst: str, mapping: list[tuple[str, str]] | None = None, **kw: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"id": f"{src}__{dst}", "source": src, "target": dst}
    if mapping:
        e["mapping"] = [{"from": f, "to": t} for f, t in mapping]
    e.update(kw)
    return e


def build(goal: str, *, include_remediation: bool = True, budget_usd: float | None = None, name: str | None = None) -> dict[str, Any]:
    findings_arr = {"type": "array", "items": S.SECURITY_FINDING}
    nodes: list[dict[str, Any]] = [
        _agent("planner", "planner", "Plan the audit"),
        {"id": "fanout", "type": "PARALLEL", "name": "Scan in parallel"},
        _agent("repo_scanner", "repository_scanner", "Scan source code"),
        _agent("dep_scanner", "dependency_scanner", "Scan dependencies"),
        _agent("researcher", "researcher", "Research external evidence"),
        {"id": "recover_inputs", "type": "RECOVERY", "name": "Degrade gracefully if a scanner is down",
         "config": {"watches": ["dep_scanner", "researcher"], "fallback_outputs": {
             "dep_scanner": {"findings": [], "summary": "Dependency scanning was unavailable in this run; no dependency findings.",
                             "dependencies_examined": 0},
             "researcher": {"evidence": [], "summary": "External research was unavailable in this run; no external evidence."}}}},
        {"id": "gather", "type": "JOIN", "name": "Merge scanner outputs",
         "config": {"required": ["repo_scanner", "dep_scanner", "researcher"], "mode": "all", "concat": {
             "findings": ["$.repo_scanner.findings", "$.dep_scanner.findings"], "evidence": ["$.researcher.evidence"]}},
         "outputSchema": {"type": "object", "properties": {"findings": findings_arr,
                                                           "evidence": {"type": "array", "items": S.EVIDENCE}},
                          "required": ["findings", "evidence"]}},
        _agent("risk_analyzer", "risk_analyzer", "Prioritize risk"),
        {"id": "verify", "type": "VERIFICATION", "name": "Independently verify findings",
         "inputSchema": {"type": "object", "properties": {"findings": findings_arr, "evidence": {"type": "array"}},
                         "required": ["findings"]},
         "outputSchema": S.VERIFICATION_OUTPUT,
         "routing": {"complexity": "high", "risk": "high", "verificationCritical": True}},
    ]
    edges: list[dict[str, Any]] = [
        _edge("planner", "fanout"),
        _edge("fanout", "repo_scanner", [("$", "plan")]),
        _edge("fanout", "dep_scanner", [("$", "plan")]),
        _edge("fanout", "researcher", [("$.stack", "stack"), ("$.focus_areas", "focus_areas")]),
        _edge("repo_scanner", "gather"), _edge("dep_scanner", "gather"), _edge("researcher", "gather"),
        _edge("gather", "risk_analyzer", [("$.findings", "findings"), ("$.evidence", "evidence")]),
        _edge("risk_analyzer", "verify", [("$.findings", "findings")]),
        _edge("gather", "verify", [("$.evidence", "evidence")]),
    ]

    report_inputs: list[tuple[str, str, list[tuple[str, str]]]] = [
        ("verify", "report", [("$.verified_findings", "findings"), ("$.summary", "verification.summary"),
                              ("$.results", "verification.results"),
                              ("$.uncertain_findings", "verification.uncertain_findings"),
                              ("$.rejected_findings", "verification.rejected_findings")]),
        ("gather", "report", [("$.evidence", "evidence")]),
    ]

    if include_remediation:
        nodes += [
            _agent("fix_planner", "fix_planner", "Propose fixes"),
            {"id": "any_fixes", "type": "CONDITION", "name": "Any fixes proposed?",
             "config": {"predicate": {"path": "$.proposals", "op": "len_gt", "value": 0}},
             "inputSchema": {"type": "object", "properties": {"proposals": {"type": "array"}}, "required": ["proposals"]}},
            {"id": "approve_fixes", "type": "APPROVAL", "name": "Approve proposed code changes",
             "config": {"kind": "approve_patch", "title": "Approve proposed code changes", "on_reject": "skip",
                        "summary": "Review the proposed patches. Nothing is applied until you approve; changes are applied only in an isolated copy."}},
            _agent("apply_tests", "test_agent", "Apply approved patches & run tests"),
            {"id": "approve_final", "type": "APPROVAL", "name": "Approve resulting diff",
             "config": {"kind": "approve_final_diff", "title": "Approve the tested diff", "on_reject": "skip",
                        "summary": "Review the final diff and test result. FORGE never pushes to your repository; export the patch to apply it."}},
        ]
        edges += [
            _edge("verify", "fix_planner", [("$.verified_findings", "findings")]),
            _edge("fix_planner", "any_fixes", [("$.proposals", "proposals")]),
            _edge("fix_planner", "approve_fixes", [("$.proposals", "proposals")]),
            _edge("any_fixes", "approve_fixes", kind="control", condition="true"),
            _edge("approve_fixes", "apply_tests", [("$.proposals", "proposals")]),
            _edge("apply_tests", "approve_final"),
        ]
        report_inputs += [("fix_planner", "report", [("$", "fix_proposals")]), ("approve_final", "report", [("$", "patch_result")])]

    nodes.append(_agent("report", "report_generator", "Write the security report"))
    for src, dst, mapping in report_inputs:
        edges.append(_edge(src, dst, mapping))
    wf = {
        "name": name or "Repository Security Audit", "description": "Plan, scan in parallel, research, prioritize, verify independently, "
        "propose fixes behind approval gates, report.", "goal": goal, "nodes": nodes, "edges": edges,
        "policies": {"allowedPermissions": ["repository.read", "search.web", "vulndb.lookup", "artifact.read", "artifact.write"]
                     + (["repository.write", "tests.run"] if include_remediation else []), "requireApprovalForWrites": True,
                     "maxParallelism": 4},
        "memoryPolicy": {"projectMemory": "read_write"},
        "budgetPolicy": {"maxUsd": budget_usd, "unpricedBehavior": "enforce_tokens_only"},
        "verificationPolicy": {"required": False},
        "outputs": ["report"],
    }
    return Workflow.from_json(wf).to_json()
