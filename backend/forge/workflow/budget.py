"""Budget enforcement. Budgets actually stop execution; they are not advisory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from forge.workflow.ir import BudgetPolicy


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    remaining_usd: float | None
    remaining_tokens: int | None
    options: tuple[str, ...] = ()  # what the user can do when blocked


def evaluate(
    policy: BudgetPolicy,
    *,
    spent_usd: float,
    spent_tokens: int,
    reserved_usd: float = 0.0,
    reserved_tokens: int = 0,
    estimate_usd: float | None,
    estimate_tokens: int,
) -> BudgetDecision:
    """Decide whether the *next* model call may start.

    estimate_usd is None when the model has no configured price (cost basis UNAVAILABLE).
    """
    rem_usd = None if policy.max_usd is None else round(policy.max_usd - spent_usd - reserved_usd, 6)
    rem_tok = None if policy.max_tokens is None else policy.max_tokens - spent_tokens - reserved_tokens
    options = ("increase_budget", "cancel", "retry_with_cheaper_model")

    if policy.max_usd is not None:
        if estimate_usd is None:
            if policy.unpriced_behavior == "block":
                return BudgetDecision(False, "Model has no configured price; USD budget cannot be enforced.",
                                      rem_usd, rem_tok, ("configure_pricing", "cancel"))
            # else: USD can't be checked for this call; token budget (if any) still applies below
        elif estimate_usd > (rem_usd or 0):
            return BudgetDecision(
                False,
                f"Budget ${policy.max_usd:.2f}: spent ${spent_usd:.4f}, next call estimated ${estimate_usd:.4f} "
                f"(remaining ${max(rem_usd or 0, 0):.4f}).",
                rem_usd, rem_tok, options)
    if rem_tok is not None and estimate_tokens > rem_tok:
        return BudgetDecision(
            False,
            f"Token budget {policy.max_tokens}: spent {spent_tokens}, next call estimated {estimate_tokens} "
            f"(remaining {max(rem_tok, 0)}).",
            rem_usd, rem_tok, options)
    return BudgetDecision(True, "within budget", rem_usd, rem_tok)


BudgetScope = Literal["workflow", "node"]
