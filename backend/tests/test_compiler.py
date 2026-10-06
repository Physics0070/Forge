import uuid

import pytest

from forge.compiler import CompileError, compile_goal
from forge.engine.runtime import router_config
from forge.registry.agents import builtin_lookup
from forge.templates import security_audit
from forge.workflow.ir import Workflow
from forge.workflow.validate import has_errors, validate_workflow
from tests.fakes import ScriptedProvider

WS = uuid.uuid4()


def _compile(spec_or_fn, goal="Audit this repository for security problems and propose fixes", **kw):
    fn = spec_or_fn if callable(spec_or_fn) else (lambda r, s, i: spec_or_fn)
    p = ScriptedProvider(fn)
    from tests.engine_helpers import Env

    env = Env()
    return compile_goal(p, workspace_id=env.ws, goal=goal, router_cfg=router_config(), **kw), p


def task(id, agent, deps=(), **kw):
    return {"id": id, "agent": agent, "title": id.replace("_", " "), "depends_on": list(deps), **kw}


GOOD = {"name": "Audit", "tasks": [
    task("plan", "planner", complexity="low"),
    task("scan", "repository_scanner", ["plan"], needs_verification=True),
    task("deps", "dependency_scanner", ["plan"], needs_verification=True),
    task("research", "researcher", ["plan"]),
    task("risk", "risk_analyzer", ["scan", "deps", "research"], complexity="high"),
    task("report", "report_generator", ["risk"])], "approvals": []}


def test_compiles_parallel_dag_with_typed_joins_and_validates():
    res, p = _compile(GOOD)
    wf = Workflow.from_json(res.workflow)
    assert not has_errors(validate_workflow(wf, agent_lookup=builtin_lookup)), res.issues
    types = {n.id: n.type.value for n in wf.nodes}
    assert types["fanout_plan"] == "PARALLEL" and types["join_risk"] == "JOIN"
    assert types["verify_scan"] == "VERIFICATION" and types["verify_deps"] == "VERIFICATION"
    join = wf.node("join_risk")
    assert set(join.config["required"]) == {"scan", "deps", "research"}
    assert "findings" in join.config["concat"] and len(join.config["concat"]["findings"]) == 2
    assert res.meta["model"] == "test-ultra" and res.meta["attempts"] == 1 and res.meta["tokens"] > 0
    assert p.calls[0]["model"] == "test-ultra"  # compilation routed to the top tier


def test_compile_prompt_lists_only_catalog_agents_and_marks_goal_as_data():
    _, p = _compile(GOOD)
    sys_msg = p.calls[0]["messages"][0].content
    assert "repository_scanner" in sys_msg and "AVAILABLE AGENTS" in sys_msg and "generalist" not in sys_msg
    assert "not instructions to you" in sys_msg


def test_writes_always_get_an_approval_gate_even_if_model_forgets():
    spec = {"name": "Fix", "tasks": [task("plan", "planner"), task("scan", "repository_scanner", ["plan"]),
                                     task("fix", "fix_planner", ["scan"]),
                                     task("apply", "test_agent", ["fix"])]}
    res, _ = _compile(spec)
    wf = Workflow.from_json(res.workflow)
    assert wf.node("approve_apply").type.value == "APPROVAL"
    assert any(e.source == "approve_apply" and e.target == "apply" for e in wf.edges)
    assert not has_errors(validate_workflow(wf, agent_lookup=builtin_lookup))


def test_write_tools_stripped_when_writes_not_allowed():
    spec = {"name": "RO", "tasks": [task("plan", "planner"), task("scan", "repository_scanner", ["plan"]),
                                    task("fix", "fix_planner", ["scan"]), task("apply", "test_agent", ["fix"])]}
    res, _ = _compile(spec, constraints={"allow_writes": False})
    wf = Workflow.from_json(res.workflow)
    assert "RepositoryWrite" not in wf.node("apply").tools and "repository.write" not in wf.node("apply").permissions
    assert not has_errors(validate_workflow(wf, agent_lookup=builtin_lookup))


def test_unknown_agent_triggers_repair_then_fails_with_reason_and_suggestion():
    bad = {"name": "x", "tasks": [task("a", "wizard_agent")]}
    with pytest.raises(CompileError) as e:
        _compile(bad)
    d = e.value.to_dict()
    assert d["status"] == "COMPILATION FAILED" and "wizard_agent" in d["reason"] and d["suggestion"]


def test_repair_round_fixes_invalid_first_plan():
    plans = [{"name": "x", "tasks": [task("a", "wizard_agent")]}, GOOD]
    res, p = _compile(lambda r, s, i: plans[min(i - 1, 1)])
    assert res.meta["attempts"] == 2 and "could not be compiled" in p.calls[1]["messages"][-1].content


def test_dependency_on_unknown_task_rejected():
    with pytest.raises(CompileError):
        _compile({"name": "x", "tasks": [task("a", "planner", ["ghost"])]})


def test_cycle_in_plan_is_not_emitted():
    spec = {"name": "x", "tasks": [task("a", "planner", ["b"]), task("b", "planner", ["a"])]}
    with pytest.raises(CompileError) as e:
        _compile(spec)
    assert "Cycle" in e.value.reason or "cycle" in e.value.reason.lower() or e.value.issues


def test_too_short_goal_rejected_without_model_call():
    p = ScriptedProvider(lambda r, s, i: GOOD)
    with pytest.raises(CompileError):
        compile_goal(p, workspace_id=WS, goal="hi", router_cfg=router_config())
    assert not p.calls


def test_model_failure_surfaces_as_compile_error():
    from forge.providers.base import ProviderError

    with pytest.raises(CompileError) as e:
        _compile(lambda r, s, i: ProviderError("auth", "bad key"))
    assert "planning model failed" in e.value.reason


def test_compile_calls_are_metered():
    from forge.db import session_scope
    from forge.models import ModelCall

    _compile(GOOD)
    with session_scope() as db:
        rows = db.query(ModelCall).filter(ModelCall.purpose == "compile").all()
        assert len(rows) == 1 and rows[0].total_tokens == 150 and rows[0].run_id is None


# ----------------------------------------------------- the shipped template
@pytest.mark.parametrize("remediation", [True, False])
def test_security_template_is_valid_ir(remediation):
    wf = Workflow.from_json(security_audit.build("audit", include_remediation=remediation))
    assert not validate_workflow(wf, agent_lookup=builtin_lookup)
    kinds = {n.type.value for n in wf.nodes}
    assert {"AGENT", "PARALLEL", "JOIN", "VERIFICATION", "RECOVERY"} <= kinds
    assert ("APPROVAL" in kinds) == remediation and ("CONDITION" in kinds) == remediation
    assert wf.content_hash() == Workflow.from_json(wf.to_json()).content_hash()
