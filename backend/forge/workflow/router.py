"""Explainable, deterministic model router. Not ML-optimised and never claimed to be.

score = complexity + risk (+ verification bonus)         each factor 0..2
score <= nano_max   -> nano     (fast, cheap, routine work)
score <= super_max  -> super    (balanced reasoning)
otherwise           -> ultra    (hard reasoning, high risk, verification-critical)

Then adjusted for: latency requirement, remaining budget, configured-model availability.
Thresholds are configurable via RouterConfig.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from forge.workflow.ir import Routing

TIERS = ("nano", "super", "ultra")
_LEVEL = {"low": 0, "medium": 1, "high": 2}


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class RouterConfig:
    nano_max: int = 1
    super_max: int = 3
    verification_bonus: int = 2
    # models by tier; empty string => tier not configured/available
    models: dict[str, str] = field(default_factory=dict)
    # USD per 1M tokens (blended) by tier for budget-aware downgrade; None => unknown, no budget downgrade
    tier_cost_per_mtok: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    fallback_tier: str | None
    fallback_model: str | None
    reasons: tuple[str, ...]
    score: int

    def to_dict(self) -> dict:
        return {"tier": self.tier, "model": self.model, "fallbackTier": self.fallback_tier,
                "fallbackModel": self.fallback_model, "reasons": list(self.reasons), "score": self.score}


def _available(cfg: RouterConfig) -> list[str]:
    return [t for t in TIERS if cfg.models.get(t)]


def route(
    routing: Routing,
    cfg: RouterConfig,
    *,
    explicit_model: str | None = None,
    est_tokens: int = 0,
    remaining_usd: float | None = None,
) -> RoutingDecision:
    avail = _available(cfg)
    reasons: list[str] = []

    if explicit_model:
        tier = next((t for t in TIERS if cfg.models.get(t) == explicit_model), "custom")
        fb_tier = _fallback(tier, avail)
        return RoutingDecision(tier, explicit_model, fb_tier, cfg.models.get(fb_tier) if fb_tier else None,
                               ("model explicitly set on node",), -1)
    if not avail:
        raise RoutingError("No models are configured. Set NEBIUS_MODEL_NANO/SUPER/ULTRA (or NEBIUS_MODEL_DEFAULT).")

    score = _LEVEL[routing.complexity] + _LEVEL[routing.risk] + (cfg.verification_bonus if routing.verification_critical else 0)
    reasons.append(f"complexity={routing.complexity}, risk={routing.risk}"
                   + (", verification-critical" if routing.verification_critical else "") + f" -> score {score}")
    tier = "nano" if score <= cfg.nano_max else "super" if score <= cfg.super_max else "ultra"
    reasons.append(f"score {score} maps to tier '{tier}'")

    if routing.latency == "fast" and tier == "ultra" and not routing.verification_critical:
        tier = "super"
        reasons.append("latency=fast: stepped down ultra -> super (not verification-critical)")
    elif routing.latency == "relaxed" and tier == "super" and routing.risk != "low":
        tier = "ultra"
        reasons.append("latency=relaxed with elevated risk: stepped up super -> ultra")

    if remaining_usd is not None and est_tokens:
        while tier != "nano":
            price = cfg.tier_cost_per_mtok.get(tier)
            if price is None or price * est_tokens / 1_000_000 <= remaining_usd:
                break
            lower = TIERS[TIERS.index(tier) - 1]
            reasons.append(f"budget: estimated {tier} cost exceeds remaining ${remaining_usd:.4f}; stepped down to {lower}")
            tier = lower

    if tier not in avail:
        # nearest configured tier; prefer the higher one for quality unless that breaks the latency intent
        order = sorted(avail, key=lambda t: (abs(TIERS.index(t) - TIERS.index(tier)), -TIERS.index(t)))
        chosen = order[0]
        reasons.append(f"tier '{tier}' is not configured; using '{chosen}'")
        tier = chosen

    fb = _fallback(tier, avail)
    if fb:
        reasons.append(f"fallback: '{fb}'")
    return RoutingDecision(tier, cfg.models[tier], fb, cfg.models.get(fb) if fb else None, tuple(reasons), score)


def _fallback(tier: str, avail: list[str]) -> str | None:
    """Fallback is a *different* model: one tier lower for ultra (availability/latency), else one tier up."""
    others = [t for t in avail if t != tier]
    if not others:
        return None
    if tier == "ultra":
        return max(others, key=TIERS.index)
    if tier == "custom":
        return others[-1]
    higher = [t for t in others if TIERS.index(t) > TIERS.index(tier)]
    return higher[0] if higher else others[-1]
