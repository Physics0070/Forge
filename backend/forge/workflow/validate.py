"""Static validation of a Workflow IR. Runs on compile, on edit, and again before approval + execution."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Literal

import jsonschema

from forge.registry.tools import PERMISSIONS, TOOLS
from forge.workflow.ir import NodeType, Workflow, WorkflowNode

Severity = Literal["error", "warning"]
AgentLookup = Callable[[str], Any]  # agent id -> object with .permissions (iterable[str]) or None


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _topo(wf: Workflow) -> tuple[list[str], list[str]]:
    """Kahn's algorithm. Returns (order, nodes_left_in_cycles)."""
    ids = [n.id for n in wf.nodes]
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in wf.edges:
        if e.source in adj and e.target in indeg:
            adj[e.source].append(e.target)
            indeg[e.target] += 1
    queue = [i for i in ids if indeg[i] == 0]
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return order, [i for i in ids if i not in set(order)]


def ancestors(wf: Workflow, node_id: str) -> set[str]:
    rev: dict[str, list[str]] = {}
    for e in wf.edges:
        rev.setdefault(e.target, []).append(e.source)
    seen: set[str] = set()
    stack = list(rev.get(node_id, []))
    while stack:
        cur = stack.pop()
        if cur not in seen:
            seen.add(cur)
            stack.extend(rev.get(cur, []))
    return seen


def _schema_props(schema: dict[str, Any]) -> dict[str, Any] | None:
    return schema.get("properties") if isinstance(schema, dict) else None


def _resolve_path(schema: dict[str, Any], path: str) -> dict[str, Any] | None | Literal["ANY"]:
    """Resolve `a.b.c` through object properties. 'ANY' when the schema is open at that point."""
    cur: Any = schema
    for part in [p for p in path.split(".") if p]:
        if not isinstance(cur, dict):
            return None
        if cur.get("type") == "array" or "items" in cur:
            return "ANY"
        props = cur.get("properties")
        if props is None:
            return "ANY" if cur.get("additionalProperties", True) is not False else None
        if part not in props:
            return None
        cur = props[part]
    return cur


def validate_workflow(
    wf: Workflow,
    *,
    agent_lookup: AgentLookup | None = None,
    known_models: set[str] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    add = lambda sev, code, msg, **kw: issues.append(Issue(sev, code, msg, **kw))  # noqa: E731

    ids = [n.id for n in wf.nodes]
    if not wf.nodes:
        add("error", "EMPTY_WORKFLOW", "Workflow has no nodes.")
        return issues
    for dup in {i for i in ids if ids.count(i) > 1}:
        add("error", "DUPLICATE_NODE_ID", f"Node id '{dup}' is used more than once.", node_id=dup)
    eids = [e.id for e in wf.edges]
    for dup in {i for i in eids if eids.count(i) > 1}:
        add("error", "DUPLICATE_EDGE_ID", f"Edge id '{dup}' is used more than once.", edge_id=dup)
    if issues:
        return issues
    nodes = {n.id: n for n in wf.nodes}

    # -- edges reference real nodes; no self loops
    for e in wf.edges:
        for end in (e.source, e.target):
            if end not in nodes:
                add("error", "UNKNOWN_NODE_REF", f"Edge '{e.id}' references unknown node '{end}'.", edge_id=e.id)
        if e.source == e.target:
            add("error", "SELF_LOOP", f"Edge '{e.id}' connects node '{e.source}' to itself.", edge_id=e.id)
    if any(i.code in ("UNKNOWN_NODE_REF", "SELF_LOOP") for i in issues):
        return issues

    # -- cycles
    _, cyc = _topo(wf)
    if cyc:
        add("error", "CYCLE", f"Cycle detected involving: {', '.join(sorted(cyc))}. Use a RETRY node for loops.")
        return issues

    # -- schemas themselves must be valid JSON Schema
    for n in wf.nodes:
        for label, sch in (("inputSchema", n.input_schema), ("outputSchema", n.output_schema)):
            try:
                jsonschema.Draft202012Validator.check_schema(sch)
            except jsonschema.SchemaError as exc:
                add("error", "INVALID_SCHEMA", f"Node '{n.id}' {label} is not valid JSON Schema: {exc.message}", node_id=n.id)

    # -- reachability / structure
    roots = [i for i in ids if not wf.incoming(i)]
    reach: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        if cur in reach:
            continue
        reach.add(cur)
        stack.extend(e.target for e in wf.outgoing(cur))
    for i in ids:
        if i not in reach:
            add("error", "UNREACHABLE_NODE", f"Node '{i}' cannot be reached from any entry node.", node_id=i)
    # weakly connected components
    comp: dict[str, str] = {i: i for i in ids}

    def find(x: str) -> str:
        while comp[x] != x:
            comp[x] = comp[comp[x]]
            x = comp[x]
        return x

    for e in wf.edges:
        comp[find(e.source)] = find(e.target)
    if len({find(i) for i in ids}) > 1:
        add("warning", "DISCONNECTED_GRAPH", "Workflow has disconnected parts that do not feed each other.")
    sinks = [i for i in ids if not wf.outgoing(i)]
    for o in wf.outputs:
        if o not in nodes:
            add("error", "UNKNOWN_OUTPUT", f"Declared output '{o}' is not a node.")

    # -- per-node checks
    for n in wf.nodes:
        _check_node(wf, n, nodes, agent_lookup, known_models, add)

    # -- per-edge handoff typing
    for e in wf.edges:
        src, dst = nodes[e.source], nodes[e.target]
        if e.kind == "data" and src.type == NodeType.CONDITION and e.condition not in ("true", "false"):
            add("error", "CONDITION_EDGE_LABEL", f"Edge '{e.id}' leaves a CONDITION node and must have condition 'true' or 'false'.", edge_id=e.id)
        if e.condition and src.type != NodeType.CONDITION:
            add("warning", "STRAY_CONDITION", f"Edge '{e.id}' has a condition but its source is not a CONDITION node.", edge_id=e.id)
        for m in e.mapping:
            if m.source.startswith("$workflow") or m.source == "$const":
                continue
            path = m.source[2:] if m.source.startswith("$.") else m.source.lstrip("$")
            if _resolve_path(src.output_schema, path) is None:
                add("error", "INVALID_MAPPING", f"Edge '{e.id}': '{m.source}' does not exist in {src.id}.outputSchema.", edge_id=e.id)
        if not e.mapping and e.kind == "data":
            _check_passthrough(e.id, src, dst, add)

    # -- required inputs satisfied
    for n in wf.nodes:
        required = set(n.input_schema.get("required", [])) if isinstance(n.input_schema, dict) else set()
        if not required:
            continue
        provided: set[str] = set()
        wired = False
        for e in wf.incoming(n.id):
            if e.kind != "data":
                continue
            wired = True
            if e.mapping:
                provided.update(m.target.split(".")[0] for m in e.mapping)
            else:
                props = _schema_props(nodes[e.source].output_schema)
                provided.update(props.keys() if props else required)  # open schema: can't prove otherwise
        if not wf.incoming(n.id):
            continue  # entry node: satisfied from the run input at execution time
        missing = required - provided
        if missing and wired:
            add("error", "MISSING_INPUT", f"Node '{n.id}' requires {sorted(missing)} but no incoming edge provides it.", node_id=n.id)

    # -- capability-aware write safety
    for n in wf.nodes:
        if n.type == NodeType.AGENT and any(TOOLS[t].requires_approval for t in n.tools if t in TOOLS):
            if wf.policies.require_approval_for_writes and not any(
                nodes[a].type == NodeType.APPROVAL for a in ancestors(wf, n.id)
            ):
                add("error", "WRITE_WITHOUT_APPROVAL",
                    f"Node '{n.id}' uses a write tool but has no APPROVAL node upstream.", node_id=n.id)

    if not sinks:
        add("error", "NO_SINK", "Workflow has no terminal node.")
    return issues


def _check_passthrough(edge_id: str, src: WorkflowNode, dst: WorkflowNode, add) -> None:
    sp, dp = _schema_props(src.output_schema), _schema_props(dst.input_schema)
    if sp is None or dp is None:
        return
    req = set(dst.input_schema.get("required", []))
    if req and not req <= set(sp):
        add("error", "INVALID_MAPPING",
            f"Edge '{edge_id}' passes {src.id} output straight to {dst.id}, which requires {sorted(req - set(sp))} that it does not produce.",
            edge_id=edge_id)


def _check_node(wf, n: WorkflowNode, nodes, agent_lookup, known_models, add) -> None:
    anc = ancestors(wf, n.id)
    for t in n.tools:
        if t not in TOOLS:
            add("error", "UNKNOWN_TOOL", f"Node '{n.id}' uses unknown tool '{t}'.", node_id=n.id)
            continue
        missing = [p for p in TOOLS[t].permissions if p not in n.permissions]
        if missing:
            add("error", "PERMISSION_CONFLICT",
                f"Node '{n.id}' uses tool '{t}' but lacks permission(s) {missing}.", node_id=n.id)
    for p in n.permissions:
        if p not in PERMISSIONS:
            add("error", "UNKNOWN_PERMISSION", f"Node '{n.id}' requests unknown permission '{p}'.", node_id=n.id)
        elif p not in wf.policies.allowed_permissions:
            add("error", "PERMISSION_CONFLICT",
                f"Node '{n.id}' requests '{p}', which workflow policy does not allow.", node_id=n.id)
    if n.agent_id and agent_lookup:
        agent = agent_lookup(n.agent_id)
        if agent is None:
            add("error", "UNKNOWN_AGENT", f"Node '{n.id}' references unknown agent '{n.agent_id}'.", node_id=n.id)
        else:
            extra = [p for p in n.permissions if p not in set(agent.permissions)]
            if extra:
                add("error", "PERMISSION_CONFLICT",
                    f"Node '{n.id}' grants {extra} that agent '{n.agent_id}' is not allowed to hold.", node_id=n.id)
    if n.type == NodeType.AGENT and not n.agent_id:
        add("error", "MISSING_AGENT", f"AGENT node '{n.id}' has no agentId.", node_id=n.id)
    if n.model and known_models is not None and n.model not in known_models:
        add("warning", "UNKNOWN_MODEL", f"Node '{n.id}' model '{n.model}' is not in the configured model list.", node_id=n.id)

    cfg = n.config
    if n.type == NodeType.JOIN:
        req = cfg.get("required")
        direct = {e.source for e in wf.incoming(n.id)}
        if len(direct) < 2:
            add("warning", "JOIN_SINGLE_INPUT", f"JOIN '{n.id}' has fewer than two upstream branches.", node_id=n.id)
        for r in req or []:
            if r not in nodes or r not in anc and r not in direct:
                add("error", "IMPOSSIBLE_DEPENDENCY", f"JOIN '{n.id}' requires '{r}', which is not upstream of it.", node_id=n.id)
    elif n.type == NodeType.PARALLEL:
        if len(wf.outgoing(n.id)) < 2:
            add("warning", "PARALLEL_SINGLE_BRANCH", f"PARALLEL '{n.id}' fans out to fewer than two nodes.", node_id=n.id)
    elif n.type == NodeType.CONDITION:
        if not isinstance(cfg.get("predicate"), dict):
            add("error", "CONDITION_PREDICATE", f"CONDITION '{n.id}' needs config.predicate {{path, op, value}}.", node_id=n.id)
        labels = {e.condition for e in wf.outgoing(n.id)}
        if not labels & {"true", "false"}:
            add("error", "CONDITION_NO_BRANCHES", f"CONDITION '{n.id}' has no true/false outgoing edges.", node_id=n.id)
    elif n.type == NodeType.RETRY:
        tgt = cfg.get("target")
        if tgt not in nodes:
            add("error", "RETRY_TARGET", f"RETRY '{n.id}' targets unknown node '{tgt}'.", node_id=n.id)
        elif tgt not in {e.source for e in wf.incoming(n.id)}:
            add("error", "IMPOSSIBLE_DEPENDENCY", f"RETRY '{n.id}' target '{tgt}' must be a direct upstream node.", node_id=n.id)
        elif len(wf.outgoing(tgt)) > 1:
            add("warning", "RETRY_TARGET_SHARED", f"RETRY '{n.id}' target '{tgt}' also feeds other nodes, which will not see re-run output.", node_id=n.id)
        if not isinstance(cfg.get("until"), dict):
            add("error", "RETRY_PREDICATE", f"RETRY '{n.id}' needs config.until {{path, op, value}}.", node_id=n.id)
    elif n.type == NodeType.RECOVERY:
        watches = cfg.get("watches") or []
        if not watches:
            add("error", "RECOVERY_WATCHES", f"RECOVERY '{n.id}' must list config.watches.", node_id=n.id)
        for w in watches:
            if w not in nodes:
                add("error", "IMPOSSIBLE_DEPENDENCY", f"RECOVERY '{n.id}' watches unknown node '{w}'.", node_id=n.id)
            elif w == n.id or n.id in ancestors(wf, w):
                add("error", "IMPOSSIBLE_DEPENDENCY", f"RECOVERY '{n.id}' cannot watch '{w}' (it depends on this node).", node_id=n.id)
    elif n.type == NodeType.TRANSFORM:
        if not cfg.get("select") and not cfg.get("set"):
            add("warning", "TRANSFORM_NOOP", f"TRANSFORM '{n.id}' has neither config.select nor config.set.", node_id=n.id)
    elif n.type == NodeType.VERIFICATION:
        if not wf.incoming(n.id):
            add("error", "VERIFICATION_NO_INPUT", f"VERIFICATION '{n.id}' has nothing upstream to verify.", node_id=n.id)
    elif n.type == NodeType.APPROVAL:
        if not wf.outgoing(n.id):
            add("warning", "APPROVAL_DEAD_END", f"APPROVAL '{n.id}' gates nothing.", node_id=n.id)


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)
