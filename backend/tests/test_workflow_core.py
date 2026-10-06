import itertools
import random
from types import SimpleNamespace

import pytest

from forge.workflow import budget as budget_mod
from forge.workflow import retry as retry_mod
from forge.workflow.ir import BudgetPolicy, RetryPolicy, Routing, Workflow
from forge.workflow.router import RouterConfig, RoutingError, route
from forge.workflow.state import (NODE_TRANSITIONS, NodeState, RunState, IllegalTransition,
                                  check_node_transition, check_run_transition)
from forge.workflow.validate import has_errors, validate_workflow


def node(id, type="AGENT", **kw):
    base = {"id": id, "type": type}
    if type == "AGENT":
        base["agentId"] = "A"
    base.update(kw)
    return base


def wf(nodes, edges=(), **kw):
    return Workflow.from_json({"name": "t", "goal": "g", "nodes": nodes,
                               "edges": [{"id": f"e{i}", **e} for i, e in enumerate(edges)], **kw})


def codes(issues, sev="error"):
    return {i.code for i in issues if i.severity == sev}


AGENTS = {"A": SimpleNamespace(permissions=["repository.read", "search.web", "artifact.write", "artifact.read"])}
lookup = AGENTS.get

# ----------------------------------------------------------------- IR
def test_ir_roundtrip_and_hash_stable():
    w = wf([node("a"), node("b")], [{"source": "a", "target": "b"}])
    again = Workflow.from_json(w.to_json())
    assert again.content_hash() == w.content_hash()
    w.name = "renamed"
    assert w.content_hash() != again.content_hash()


def test_ir_rejects_unknown_fields_and_bad_ids():
    with pytest.raises(Exception):
        Workflow.from_json({"name": "t", "goal": "g", "nodes": [{"id": "1bad", "type": "AGENT"}]})
    with pytest.raises(Exception):
        Workflow.from_json({"name": "t", "goal": "g", "nodes": [], "surprise": 1})


# ----------------------------------------------------------- validation
def test_valid_simple_graph():
    w = wf([node("a"), node("b")], [{"source": "a", "target": "b"}])
    assert not has_errors(validate_workflow(w, agent_lookup=lookup))


def test_cycle_detected():
    w = wf([node("a"), node("b")], [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}])
    assert "CYCLE" in codes(validate_workflow(w, agent_lookup=lookup))


def test_self_loop_and_unknown_ref():
    w = wf([node("a")], [{"source": "a", "target": "a"}])
    assert "SELF_LOOP" in codes(validate_workflow(w))
    w = wf([node("a")], [{"source": "a", "target": "ghost"}])
    assert "UNKNOWN_NODE_REF" in codes(validate_workflow(w))


def test_duplicate_ids():
    assert "DUPLICATE_NODE_ID" in codes(validate_workflow(wf([node("a"), node("a")])))


def test_disconnected_is_warning():
    w = wf([node("a"), node("b"), node("c")], [{"source": "a", "target": "b"}])
    assert "DISCONNECTED_GRAPH" in codes(validate_workflow(w, agent_lookup=lookup), "warning")


def test_missing_tool_and_permission_conflict():
    w = wf([node("a", tools=["Nope"])])
    assert "UNKNOWN_TOOL" in codes(validate_workflow(w, agent_lookup=lookup))
    w = wf([node("a", tools=["RepositoryRead"], permissions=[])])
    assert "PERMISSION_CONFLICT" in codes(validate_workflow(w, agent_lookup=lookup))


def test_node_cannot_exceed_agent_or_workflow_permissions():
    w = wf([node("a", permissions=["repository.write"])])
    assert "PERMISSION_CONFLICT" in codes(validate_workflow(w, agent_lookup=lookup))
    w = wf([node("a", permissions=["tests.run"])], policies={"allowedPermissions": ["repository.read"]})
    assert "PERMISSION_CONFLICT" in codes(validate_workflow(w, agent_lookup=lookup))


def test_write_tool_requires_upstream_approval():
    w = wf([node("a", tools=["RepositoryWrite"], permissions=["repository.write"])],
           policies={"allowedPermissions": ["repository.write"]})
    assert "WRITE_WITHOUT_APPROVAL" in codes(validate_workflow(w))
    w = wf([node("ap", "APPROVAL"), node("a", tools=["RepositoryWrite"], permissions=["repository.write"])],
           [{"source": "ap", "target": "a"}], policies={"allowedPermissions": ["repository.write"]})
    assert "WRITE_WITHOUT_APPROVAL" not in codes(validate_workflow(w))


def test_missing_required_input():
    src = node("a", outputSchema={"type": "object", "properties": {"x": {"type": "string"}}})
    dst = node("b", inputSchema={"type": "object", "properties": {"x": {}, "y": {}}, "required": ["x", "y"]})
    w = wf([src, dst], [{"source": "a", "target": "b", "mapping": [{"from": "$.x", "to": "x"}]}])
    assert "MISSING_INPUT" in codes(validate_workflow(w, agent_lookup=lookup))


def test_invalid_mapping_path():
    src = node("a", outputSchema={"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": False})
    w = wf([src, node("b")], [{"source": "a", "target": "b", "mapping": [{"from": "$.nope", "to": "z"}]}])
    assert "INVALID_MAPPING" in codes(validate_workflow(w, agent_lookup=lookup))


def test_passthrough_missing_fields_flagged():
    src = node("a", outputSchema={"type": "object", "properties": {"x": {}}})
    dst = node("b", inputSchema={"type": "object", "properties": {"y": {}}, "required": ["y"]})
    w = wf([src, dst], [{"source": "a", "target": "b"}])
    assert "INVALID_MAPPING" in codes(validate_workflow(w, agent_lookup=lookup))


def test_invalid_json_schema_rejected():
    w = wf([node("a", outputSchema={"type": "not-a-type"})])
    assert "INVALID_SCHEMA" in codes(validate_workflow(w, agent_lookup=lookup))


def test_join_requires_upstream_nodes():
    w = wf([node("a"), node("b"), node("j", "JOIN", config={"required": ["a", "zzz"]})],
           [{"source": "a", "target": "j"}, {"source": "b", "target": "j"}])
    assert "IMPOSSIBLE_DEPENDENCY" in codes(validate_workflow(w, agent_lookup=lookup))


def test_retry_and_recovery_dependencies():
    w = wf([node("a"), node("r", "RETRY", config={"target": "a", "until": {"path": "$.ok", "op": "eq", "value": True}})],
           [{"source": "a", "target": "r"}])
    assert not has_errors(validate_workflow(w, agent_lookup=lookup))
    w = wf([node("a"), node("r", "RETRY", config={"target": "zzz", "until": {}})], [{"source": "a", "target": "r"}])
    assert "RETRY_TARGET" in codes(validate_workflow(w, agent_lookup=lookup))
    # recovery cannot watch its own descendant
    w = wf([node("a"), node("rec", "RECOVERY", config={"watches": ["b"]}), node("b")],
           [{"source": "a", "target": "rec"}, {"source": "rec", "target": "b"}])
    assert "IMPOSSIBLE_DEPENDENCY" in codes(validate_workflow(w, agent_lookup=lookup))


def test_condition_needs_labelled_edges():
    w = wf([node("c", "CONDITION", config={"predicate": {"path": "$.x", "op": "gt", "value": 1}}), node("b")],
           [{"source": "c", "target": "b"}])
    assert {"CONDITION_EDGE_LABEL", "CONDITION_NO_BRANCHES"} <= codes(validate_workflow(w, agent_lookup=lookup))


# ------------------------------------------------------------ state machine
def test_every_node_transition_pair_is_decided():
    for s, d in itertools.product(NodeState, NodeState):
        if d in NODE_TRANSITIONS[s]:
            assert check_node_transition(s, d) == d
        else:
            with pytest.raises(IllegalTransition):
                check_node_transition(s, d)


def test_terminal_states_are_sticky():
    for s in (NodeState.CANCELLED, NodeState.SKIPPED):
        for d in NodeState:
            with pytest.raises(IllegalTransition):
                check_node_transition(s, d)
    with pytest.raises(IllegalTransition):
        check_node_transition("SUCCESS", "RUNNING")
    with pytest.raises(IllegalTransition):
        check_run_transition(RunState.SUCCESS, RunState.RUNNING)
    check_run_transition(RunState.FAILED, RunState.RUNNING)  # user retry


def test_arbitrary_state_strings_rejected():
    with pytest.raises(ValueError):
        check_node_transition("PENDING", "HAPPY")


# ------------------------------------------------------------------ retry
P = RetryPolicy.model_validate({"maxAttempts": 3, "baseDelayS": 1, "jitter": 0, "fallbackModel": "fb"})


def test_retry_transient_with_backoff():
    d1 = retry_mod.decide(P, retry_mod.ErrorClass.TRANSIENT, 1)
    d2 = retry_mod.decide(P, retry_mod.ErrorClass.TRANSIENT, 2)
    assert d1.retry and d1.delay_s == 1 and d2.retry and d2.delay_s == 2
    assert d2.use_fallback  # last allowed attempt switches to fallback model
    assert not retry_mod.decide(P, retry_mod.ErrorClass.TRANSIENT, 3).retry


def test_never_retry_permanent_policy_budget():
    for c in (retry_mod.ErrorClass.PERMANENT, retry_mod.ErrorClass.POLICY_VIOLATION, retry_mod.ErrorClass.BUDGET):
        assert not retry_mod.decide(P, c, 1).retry


def test_validation_failures_bounded():
    assert retry_mod.decide(P, retry_mod.ErrorClass.VALIDATION_FAILURE, 1, validation_failures_so_far=1).retry
    assert not retry_mod.decide(P, retry_mod.ErrorClass.VALIDATION_FAILURE, 2, validation_failures_so_far=2).retry


def test_jitter_is_bounded():
    pol = RetryPolicy.model_validate({"maxAttempts": 5, "baseDelayS": 4, "jitter": 0.5})
    rng = random.Random(1)
    for _ in range(50):
        d = retry_mod.decide(pol, retry_mod.ErrorClass.TIMEOUT, 1, rng=rng)
        assert 4 <= d.delay_s <= 6


# ----------------------------------------------------------------- budget
BP = BudgetPolicy.model_validate({"maxUsd": 1.0})


def test_budget_blocks_before_overspend():
    d = budget_mod.evaluate(BP, spent_usd=0.96, spent_tokens=0, estimate_usd=0.12, estimate_tokens=1000)
    assert not d.allowed and "retry_with_cheaper_model" in d.options
    assert budget_mod.evaluate(BP, spent_usd=0.5, spent_tokens=0, estimate_usd=0.12, estimate_tokens=1000).allowed


def test_budget_counts_reservations_for_parallel_calls():
    d = budget_mod.evaluate(BP, spent_usd=0.5, spent_tokens=0, reserved_usd=0.45, estimate_usd=0.1, estimate_tokens=1)
    assert not d.allowed


def test_unpriced_models_fall_back_to_tokens_or_block():
    tok = BudgetPolicy.model_validate({"maxUsd": 1.0, "maxTokens": 1000})
    assert budget_mod.evaluate(tok, spent_usd=0, spent_tokens=0, estimate_usd=None, estimate_tokens=500).allowed
    assert not budget_mod.evaluate(tok, spent_usd=0, spent_tokens=900, estimate_usd=None, estimate_tokens=500).allowed
    blk = BudgetPolicy.model_validate({"maxUsd": 1.0, "unpricedBehavior": "block"})
    assert not budget_mod.evaluate(blk, spent_usd=0, spent_tokens=0, estimate_usd=None, estimate_tokens=1).allowed


def test_no_budget_means_allowed():
    assert budget_mod.evaluate(BudgetPolicy(), spent_usd=99, spent_tokens=99, estimate_usd=5, estimate_tokens=5).allowed


# ----------------------------------------------------------------- router
CFG = RouterConfig(models={"nano": "n", "super": "s", "ultra": "u"},
                   tier_cost_per_mtok={"nano": 0.1, "super": 1.0, "ultra": 10.0})


def R(c="medium", r="low", l="normal", v=False):
    return Routing.model_validate({"complexity": c, "risk": r, "latency": l, "verificationCritical": v})


def test_router_tiers():
    assert route(R("low", "low"), CFG).tier == "nano"
    assert route(R("medium", "low"), CFG).tier == "nano"
    assert route(R("medium", "medium"), CFG).tier == "super"
    assert route(R("high", "high"), CFG).tier == "ultra"
    assert route(R("low", "low", v=True), CFG).tier == "super"
    assert route(R("medium", "medium", v=True), CFG).tier == "ultra"


def test_router_explicit_model_wins_and_has_reason():
    d = route(R("high", "high"), CFG, explicit_model="s")
    assert d.model == "s" and "explicitly" in d.reasons[0]


def test_router_latency_and_budget_adjustments():
    d = route(R("high", "high", "fast"), CFG)
    assert d.tier == "super"
    d = route(R("high", "high"), CFG, est_tokens=1_000_000, remaining_usd=2.0)
    assert d.tier == "super" and any("budget" in r for r in d.reasons)
    d = route(R("high", "high", v=True, l="fast"), CFG)
    assert d.tier == "ultra"  # verification-critical is never downgraded for latency


def test_router_handles_missing_tiers():
    only = RouterConfig(models={"super": "s"})
    for c in ("low", "high"):
        d = route(R(c, c), only)
        assert d.model == "s" and d.fallback_model is None
    with pytest.raises(RoutingError):
        route(R(), RouterConfig(models={}))


def test_router_fallback_is_different_model():
    d = route(R("high", "high"), CFG)
    assert d.tier == "ultra" and d.fallback_model == "s"
    d = route(R("low", "low"), CFG)
    assert d.fallback_model and d.fallback_model != d.model
