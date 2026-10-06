"""Provider-agnostic model interface. The execution engine depends only on this module."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

ErrorKind = Literal[
    "transient",  # network, 5xx — retry with backoff
    "rate_limited",  # 429 — retry honoring retry_after
    "timeout",
    "auth",  # bad key — permanent
    "permanent",  # 4xx other than 429 — retrying will not help
    "invalid_output",  # model returned something that violates the requested schema
    "unavailable",  # provider not configured (e.g. missing API key / model id)
]
RETRYABLE: set[str] = {"transient", "rate_limited", "timeout"}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None,
                 retry_after: float | None = None, request_id: str | None = None):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.status = status
        self.retry_after = retry_after
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    messages: list[Message]
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


@dataclass
class GenerationResult:
    text: str
    model: str  # model id the provider actually reports serving
    requested_model: str
    usage: Usage
    latency_ms: int
    finish_reason: str | None = None
    request_id: str | None = None
    attempts: int = 1
    parsed: Any = None  # populated by structured_generate
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenCount:
    count: int
    basis: Literal["ACTUAL", "ESTIMATED"]


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...

    @abstractmethod
    def structured_generate(self, req: GenerationRequest, schema: dict[str, Any], schema_name: str) -> GenerationResult:
        """Return a result whose `.parsed` is validated against `schema`, or raise ProviderError(invalid_output)."""

    @abstractmethod
    def stream(self, req: GenerationRequest) -> Iterator[str]: ...

    @abstractmethod
    def count_tokens(self, messages: list[Message]) -> TokenCount:
        """Pre-flight estimate for budgeting. Post-call usage always comes from the provider response."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...
