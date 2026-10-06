"""Tiny, safe data-addressing language used by edges, CONDITION/RETRY predicates and TRANSFORM nodes.

Paths: `$.a.b[0].c` (document root), `$workflow.x` handled by the engine (run input).
No eval, no recursion into user-supplied code: only dict keys and list indexes.
"""
from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"\.?([A-Za-z0-9_\-]+)|\[(\d+)\]")
_MISSING = object()


def _split(path: str) -> list[str | int]:
    p = path.strip()
    if p in ("$", ""):
        return []
    if p.startswith("$."):
        p = p[2:]
    elif p.startswith("$"):
        p = p[1:]
    out: list[str | int] = []
    pos = 0
    p = "." + p if p and not p.startswith(("[", ".")) else p
    while pos < len(p):
        m = _TOKEN.match(p, pos)
        if not m:
            raise ValueError(f"invalid path: {path!r}")
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return out


def get_path(data: Any, path: str, default: Any = _MISSING) -> Any:
    cur = data
    for part in _split(path):
        try:
            cur = cur[part]
        except (KeyError, IndexError, TypeError):
            if default is _MISSING:
                raise KeyError(path) from None
            return default
    return cur


def has_path(data: Any, path: str) -> bool:
    try:
        get_path(data, path)
        return True
    except KeyError:
        return False


def set_path(dst: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ValueError("empty target path")
    cur = dst
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ------------------------------------------------------------------ predicates
def eval_predicate(pred: dict[str, Any], data: Any) -> bool:
    """{path, op, value} or {all:[...]} / {any:[...]} / {not:{...}}."""
    if "all" in pred:
        return all(eval_predicate(p, data) for p in pred["all"])
    if "any" in pred:
        return any(eval_predicate(p, data) for p in pred["any"])
    if "not" in pred:
        return not eval_predicate(pred["not"], data)
    op = pred.get("op")
    path = pred.get("path", "$")
    val = pred.get("value")
    cur = get_path(data, path, _MISSING)
    if op == "exists":
        return (cur is not _MISSING) == bool(val if val is not None else True)
    if cur is _MISSING:
        return False
    try:
        if op == "eq":
            return cur == val
        if op == "ne":
            return cur != val
        if op == "gt":
            return cur > val
        if op == "gte":
            return cur >= val
        if op == "lt":
            return cur < val
        if op == "lte":
            return cur <= val
        if op == "in":
            return cur in val
        if op == "contains":
            return val in cur
        if op == "truthy":
            return bool(cur)
        if op == "len_gt":
            return len(cur) > val
        if op == "len_gte":
            return len(cur) >= val
        if op == "len_eq":
            return len(cur) == val
    except TypeError:
        return False
    raise ValueError(f"unknown predicate op: {op!r}")


# ------------------------------------------------------------------- TRANSFORM
def apply_transform(config: dict[str, Any], data: Any) -> dict[str, Any]:
    """config.select: {target: "$.path"}   config.set: {target: literal | {"from": path} | {"concat": [paths]} | {"count": path}}"""
    out: dict[str, Any] = {}
    for target, path in (config.get("select") or {}).items():
        set_path(out, target, get_path(data, path, None))
    for target, spec in (config.get("set") or {}).items():
        if isinstance(spec, dict) and "from" in spec:
            set_path(out, target, get_path(data, spec["from"], None))
        elif isinstance(spec, dict) and "concat" in spec:
            merged: list[Any] = []
            for p in spec["concat"]:
                v = get_path(data, p, None)
                if isinstance(v, list):
                    merged.extend(v)
            set_path(out, target, merged)
        elif isinstance(spec, dict) and "count" in spec:
            v = get_path(data, spec["count"], None)
            set_path(out, target, len(v) if isinstance(v, (list, dict, str)) else 0)
        else:
            set_path(out, target, spec)
    return out
