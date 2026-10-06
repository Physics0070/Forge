"""The ONE place model calls happen: budget reservation -> provider call -> settlement -> persisted record."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import func, select

from forge.db import session_scope
from forge.engine.core import emit, lock_run
from forge.engine.runtime import NodeBlocked, NodeCtx
from forge.logging import log
from forge.models import ModelCall, Run
from forge.providers.base import GenerationRequest, GenerationResult, Message, ModelProvider, ProviderError, Usage
from forge.providers.pricing import compute_cost, load_pricing
from forge.workflow import budget as budget_mod
from forge.workflow.ir import BudgetPolicy
from forge.workflow.retry import ErrorClass, NodeError, classify_provider_error

DEFAULT_MAX_TOKENS = 3000


def _estimate(provider: ModelProvider, model: str, messages: list[Message], max_out: int) -> tuple[int, float | None]:
    est_in = provider.count_tokens(messages).count
    cost = compute_cost(model, Usage(est_in, max_out), tokens_estimated=True)
    return est_in + max_out, cost.usd


def _policy_for(run: Run) -> BudgetPolicy:
    base = (run.ir_snapshot or {}).get("budgetPolicy") or {}
    return BudgetPolicy.model_validate({**base, "maxUsd": float(run.budget_usd) if run.budget_usd is not None else None,
                                        "maxTokens": int(run.budget_tokens) if run.budget_tokens is not None else None})


def reserve(ctx: NodeCtx, model: str, est_tokens: int, est_usd: float | None) -> None:
    """Atomically check the budget (workflow + node) and reserve the estimated spend. Raises NodeBlocked."""
    blocked: dict[str, Any] | None = None
    with session_scope() as db:
        run = lock_run(db, ctx.run_id)
        policy = _policy_for(run)
        decision = budget_mod.evaluate(
            policy, spent_usd=float(run.spent_usd), spent_tokens=int(run.spent_tokens),
            reserved_usd=float(run.reserved_usd), reserved_tokens=int(run.reserved_tokens),
            estimate_usd=est_usd, estimate_tokens=est_tokens)
        node_policy = ctx.node.budget
        if decision.allowed and (node_policy.max_usd is not None or node_policy.max_tokens is not None):
            row = db.execute(select(func.coalesce(func.sum(ModelCall.cost_usd), 0), func.coalesce(func.sum(ModelCall.total_tokens), 0))
                             .where(ModelCall.run_id == run.id, ModelCall.node_id == ctx.node.id)).one()
            node_dec = budget_mod.evaluate(
                BudgetPolicy.model_validate({"maxUsd": node_policy.max_usd, "maxTokens": node_policy.max_tokens}),
                spent_usd=float(row[0]), spent_tokens=int(row[1]), estimate_usd=est_usd, estimate_tokens=est_tokens)
            if not node_dec.allowed:
                decision = node_dec
        if decision.allowed:
            run.reserved_usd = float(run.reserved_usd) + (est_usd or 0.0)
            run.reserved_tokens = int(run.reserved_tokens) + est_tokens
        else:
            blocked = {"reason": decision.reason, "remaining_usd": decision.remaining_usd,
                       "remaining_tokens": decision.remaining_tokens, "options": list(decision.options),
                       "next_estimate_usd": est_usd, "next_estimate_tokens": est_tokens, "model": model,
                       "spent_usd": float(run.spent_usd)}
            emit(db, run, "BUDGET_BLOCKED", node_id=ctx.node.id, status="BLOCKED", **blocked)
    if blocked is not None:  # raised after the event transaction committed
        raise NodeBlocked("budget", blocked)


def settle(ctx: NodeCtx, *, est_tokens: int, est_usd: float | None, record: dict[str, Any], cost_usd: float | None,
           spent_tokens: int) -> None:
    with session_scope() as db:
        run = lock_run(db, ctx.run_id)
        run.reserved_usd = max(0.0, float(run.reserved_usd) - (est_usd or 0.0))
        run.reserved_tokens = max(0, int(run.reserved_tokens) - est_tokens)
        run.spent_usd = float(run.spent_usd) + (cost_usd or 0.0)
        run.spent_tokens = int(run.spent_tokens) + spent_tokens
        mc = ModelCall(workspace_id=ctx.workspace_id, run_id=ctx.run_id, node_id=ctx.node.id, **record)
        db.add(mc)
        emit(db, run, "MODEL_CALLED", node_id=ctx.node.id, status=record["status"], model=record["model"],
             purpose=record["purpose"], input_tokens=record.get("input_tokens"), output_tokens=record.get("output_tokens"),
             latency_ms=record.get("latency_ms"), cost_usd=cost_usd, cost_basis=record.get("cost_basis"),
             error_class=record.get("error_class"))


def call_structured(ctx: NodeCtx, *, purpose: str, model: str, messages: list[Message], schema: dict[str, Any],
                    schema_name: str, max_tokens: int = DEFAULT_MAX_TOKENS, temperature: float = 0.2) -> GenerationResult:
    est_tokens, est_usd = _estimate(ctx.provider, model, messages, max_tokens)
    reserve(ctx, model, est_tokens, est_usd)
    timeout = max(5.0, min(120.0, ctx.remaining_s()))
    req = GenerationRequest(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, timeout_s=timeout)
    req_hash = hashlib.sha256(json.dumps([[m.role, m.content] for m in messages], sort_keys=True).encode()).hexdigest()
    try:
        res = ctx.provider.structured_generate(req, schema, schema_name)
    except ProviderError as exc:
        partial: GenerationResult | None = getattr(exc, "result", None)
        usage = partial.usage if partial else Usage(None, None)
        cost = compute_cost(model, usage) if partial else compute_cost(model, Usage(None, None))
        settle(ctx, est_tokens=est_tokens, est_usd=est_usd, cost_usd=cost.usd, spent_tokens=usage.total_tokens or 0,
               record=dict(purpose=purpose, provider=ctx.provider.name, model=model, status="error",
                           error_class=exc.kind, error_message=exc.message[:500], input_tokens=usage.input_tokens,
                           output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
                           latency_ms=partial.latency_ms if partial else None, cost_usd=cost.usd, cost_basis=cost.basis,
                           request_hash=req_hash, provider_request_id=exc.request_id))
        log("model_call_failed", logging.WARNING, run_id=str(ctx.run_id), node_id=ctx.node.id, model=model, kind=exc.kind)
        raise NodeError(classify_provider_error(exc), exc.message, detail={"provider_kind": exc.kind, "status": exc.status,
                                                                          "model": model}) from exc
    usage = res.usage
    estimated = usage.total_tokens is None
    if estimated:  # provider omitted usage: fall back to labelled estimates
        usage = Usage(est_tokens - max_tokens, max(1, len(res.text) // 3))
    cost = compute_cost(res.model if res.model in load_pricing() else model, usage, tokens_estimated=estimated)
    settle(ctx, est_tokens=est_tokens, est_usd=est_usd, cost_usd=cost.usd, spent_tokens=usage.total_tokens or 0,
           record=dict(purpose=purpose, provider=ctx.provider.name, model=res.model or model, status="ok",
                       input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
                       latency_ms=res.latency_ms, cost_usd=cost.usd, cost_basis=cost.basis, request_hash=req_hash,
                       provider_request_id=res.request_id))
    return res

