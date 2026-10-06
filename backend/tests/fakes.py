"""Test-only scripted model provider. Lives in tests/ so it cannot be imported by production code paths:
production wiring (forge.engine.runtime.get_provider) only ever builds NebiusProvider."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator

import jsonschema

from forge.providers.base import (GenerationRequest, GenerationResult, Message, ModelProvider, ProviderError, TokenCount,
                                  Usage)

Handler = Callable[[GenerationRequest, str, int], "dict | str | ProviderError | tuple"]


class ScriptedProvider(ModelProvider):
    """handler(req, schema_name, call_index) -> dict (JSON reply) | str (raw text) | ProviderError (raise)
    | (reply, usage_tuple)  -- records every request so tests can inspect exactly what was sent."""

    name = "scripted"

    def __init__(self, handler: Handler, *, latency_s: float = 0.0, tokens: tuple[int, int] = (100, 50)):
        self.handler = handler
        self.latency_s = latency_s
        self.tokens = tokens
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._n = 0
        self.inflight = 0
        self.max_inflight = 0

    def _next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n

    def generate(self, req: GenerationRequest) -> GenerationResult:
        return self.structured_generate(req, {"type": "object"}, "generate")

    def structured_generate(self, req: GenerationRequest, schema: dict[str, Any], schema_name: str) -> GenerationResult:
        i = self._next()
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.calls.append({"model": req.model, "schema_name": schema_name, "messages": list(req.messages), "i": i})
        try:
            if self.latency_s:
                time.sleep(self.latency_s)
            out = self.handler(req, schema_name, i)
        finally:
            with self._lock:
                self.inflight -= 1
        usage = self.tokens
        if isinstance(out, tuple):
            out, usage = out
        if isinstance(out, ProviderError):
            raise out
        text = out if isinstance(out, str) else json.dumps(out)
        res = GenerationResult(text=text, model=req.model, requested_model=req.model, usage=Usage(*usage),
                               latency_ms=int(self.latency_s * 1000) or 1, request_id=f"fake-{i}")
        try:
            from forge.providers.nebius import extract_json

            parsed = extract_json(text)
            jsonschema.validate(parsed, schema)
        except Exception as exc:
            err = ProviderError("invalid_output", f"Model output violated schema '{schema_name}': {str(exc)[:200]}")
            err.result = res  # type: ignore[attr-defined]
            raise err from exc
        res.parsed = parsed
        return res

    def stream(self, req: GenerationRequest) -> Iterator[str]:
        yield self.generate(req).text

    def count_tokens(self, messages: list[Message]) -> TokenCount:
        return TokenCount(max(1, sum(len(m.content) for m in messages) // 3), "ESTIMATED")

    def list_models(self) -> list[str]:
        return ["test-nano", "test-super", "test-ultra"]

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "latency_ms": 1, "model_count": 3}


def final(output: dict[str, Any]) -> dict[str, Any]:
    return {"action": "final", "output": output}


def tool(name: str, **inp: Any) -> dict[str, Any]:
    return {"action": "tool_call", "tool": name, "input": inp}
