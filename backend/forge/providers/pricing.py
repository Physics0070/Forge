"""Configurable model pricing. No prices are hardcoded in code paths.

`pricing.json` (or FORGE_PRICING_FILE) maps model id -> USD per 1M tokens:

    { "models": { "nvidia/nemotron-3-super-120b-a12b":
        { "input_per_mtok": 0.0, "output_per_mtok": 0.0, "source": "console 2026-10-06" } } }

A model without an entry yields cost basis UNAVAILABLE — we never invent a price.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from forge.providers.base import Usage

CostBasis = Literal["ACTUAL", "ESTIMATED", "UNAVAILABLE"]
DEFAULT_PATH = Path(__file__).resolve().parent / "pricing.json"


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float
    output_per_mtok: float
    source: str = ""


@dataclass(frozen=True)
class Cost:
    usd: float | None
    basis: CostBasis


@lru_cache
def load_pricing(path: str | None = None) -> dict[str, ModelPricing]:
    p = Path(path or os.environ.get("FORGE_PRICING_FILE") or DEFAULT_PATH)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, ModelPricing] = {}
    for model, v in (raw.get("models") or {}).items():
        out[model] = ModelPricing(float(v["input_per_mtok"]), float(v["output_per_mtok"]), str(v.get("source", "")))
    return out


def compute_cost(model: str, usage: Usage, *, tokens_estimated: bool = False,
                 pricing: dict[str, ModelPricing] | None = None) -> Cost:
    table = pricing if pricing is not None else load_pricing()
    price = table.get(model)
    if price is None or usage.input_tokens is None or usage.output_tokens is None:
        return Cost(None, "UNAVAILABLE")
    usd = (usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok) / 1_000_000
    return Cost(round(usd, 6), "ESTIMATED" if tokens_estimated else "ACTUAL")
