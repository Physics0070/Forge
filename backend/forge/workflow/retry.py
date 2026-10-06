"""Error classification + retry decisions. Deterministic failures are never retried endlessly."""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from forge.providers.base import ProviderError
from forge.workflow.ir import RetryPolicy


class ErrorClass(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PERMANENT = "permanent"
    POLICY_VIOLATION = "policy_violation"
    MODEL_FAILURE = "model_failure"  # provider/model misbehaved (empty response, malformed envelope)
    TOOL_FAILURE = "tool_failure"
    VALIDATION_FAILURE = "validation_failure"  # output/handoff violated a schema
    BUDGET = "budget"


# Never retried regardless of policy.
NEVER_RETRY = {ErrorClass.PERMANENT, ErrorClass.POLICY_VIOLATION, ErrorClass.BUDGET}


class NodeError(Exception):
    def __init__(self, error_class: ErrorClass, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.message = message
        self.detail = detail or {}


def classify_provider_error(exc: ProviderError) -> ErrorClass:
    return {
        "transient": ErrorClass.TRANSIENT,
        "rate_limited": ErrorClass.RATE_LIMITED,
        "timeout": ErrorClass.TIMEOUT,
        "auth": ErrorClass.PERMANENT,
        "permanent": ErrorClass.PERMANENT,
        "unavailable": ErrorClass.PERMANENT,
        "invalid_output": ErrorClass.VALIDATION_FAILURE,
    }[exc.kind]


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_s: float
    use_fallback: bool
    reason: str


def decide(policy: RetryPolicy, error_class: ErrorClass, attempt: int, *, validation_failures_so_far: int = 0,
           rng: random.Random | None = None) -> RetryDecision:
    """`attempt` is the 1-based number of the attempt that just failed."""
    if error_class in NEVER_RETRY:
        return RetryDecision(False, 0, False, f"{error_class.value} errors are never retried")
    if attempt >= policy.max_attempts:
        return RetryDecision(False, 0, False, f"max attempts ({policy.max_attempts}) reached")
    if error_class == ErrorClass.VALIDATION_FAILURE:
        if validation_failures_so_far > policy.validation_retries:
            return RetryDecision(False, 0, False, f"validation retries ({policy.validation_retries}) exhausted")
    elif error_class.value not in policy.retryable_errors:
        return RetryDecision(False, 0, False, f"{error_class.value} is not in retryableErrors")

    if policy.backoff == "none":
        base = 0.0
    elif policy.backoff == "fixed":
        base = policy.base_delay_s
    else:
        base = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
    jitter = (rng or random).uniform(0, base * policy.jitter) if base else 0.0
    # Fall back to the alternate model for the final allowed attempt, or after a model-quality failure.
    use_fallback = bool(policy.fallback_model) and (
        attempt + 1 >= policy.max_attempts or error_class in (ErrorClass.MODEL_FAILURE, ErrorClass.VALIDATION_FAILURE)
    )
    return RetryDecision(True, round(base + jitter, 3), use_fallback, f"retrying after {error_class.value}")
