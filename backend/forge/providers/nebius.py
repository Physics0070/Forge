"""Nebius Token Factory provider (OpenAI-compatible HTTP API, called directly)."""
from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Any, Iterator

import httpx
import jsonschema

from forge.providers.base import (
    GenerationRequest,
    GenerationResult,
    Message,
    ModelProvider,
    ProviderError,
    TokenCount,
    Usage,
)

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def extract_json(text: str) -> Any:
    """Parse model output as JSON, tolerating reasoning blocks and markdown fences."""
    cleaned = _THINK.sub("", text).strip()
    m = _FENCE.match(cleaned)
    if m:
        cleaned = m.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # last resort: outermost object/array
        for open_c, close_c in (("{", "}"), ("[", "]")):
            i, j = cleaned.find(open_c), cleaned.rfind(close_c)
            if 0 <= i < j:
                try:
                    return json.loads(cleaned[i : j + 1])
                except json.JSONDecodeError:
                    continue
        raise


class NebiusProvider(ModelProvider):
    name = "nebius"

    def __init__(self, api_key: str, base_url: str, *, max_concurrency: int = 4, max_retries: int = 2,
                 client: httpx.Client | None = None):
        self._api_key = api_key
        self._base = base_url.rstrip("/") + "/"
        self._max_retries = max_retries
        self._sem = threading.BoundedSemaphore(max_concurrency)  # per-provider concurrency limit
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderError("unavailable", "NEBIUS_API_KEY is not configured.")

    @staticmethod
    def _classify(resp: httpx.Response) -> ProviderError:
        rid = resp.headers.get("x-request-id") or resp.headers.get("request-id")
        try:
            detail = resp.json().get("error", {})
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
        except Exception:
            msg = None
        msg = msg or resp.text[:300] or f"HTTP {resp.status_code}"
        s = resp.status_code
        if s == 429:
            ra = resp.headers.get("retry-after")
            return ProviderError("rate_limited", msg, status=s, retry_after=float(ra) if ra and ra.isdigit() else None, request_id=rid)
        if s in (401, 403):
            return ProviderError("auth", msg, status=s, request_id=rid)
        if s >= 500 or s in (408, 409):
            return ProviderError("transient", msg, status=s, request_id=rid)
        return ProviderError("permanent", msg, status=s, request_id=rid)

    def _post(self, payload: dict[str, Any], timeout_s: float) -> tuple[httpx.Response, int]:
        self._require_key()
        attempts = 0
        last: ProviderError | None = None
        with self._sem:
            while attempts <= self._max_retries:
                attempts += 1
                try:
                    resp = self._client.post(self._base + "chat/completions", headers=self._headers(),
                                             json=payload, timeout=timeout_s)
                    if resp.status_code < 400:
                        return resp, attempts
                    last = self._classify(resp)
                except httpx.TimeoutException:
                    last = ProviderError("timeout", f"Request timed out after {timeout_s:.0f}s")
                except httpx.TransportError as exc:
                    last = ProviderError("transient", f"Network error: {type(exc).__name__}")
                if not last.retryable or attempts > self._max_retries:
                    break
                delay = last.retry_after if last.retry_after is not None else min(8.0, 0.5 * 2 ** (attempts - 1))
                time.sleep(delay + random.uniform(0, 0.25))
        assert last is not None
        raise last

    @staticmethod
    def _payload(req: GenerationRequest, **extra: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
        }
        if req.max_tokens:
            body["max_tokens"] = req.max_tokens
        body.update(extra)
        return body

    def _to_result(self, req: GenerationRequest, resp: httpx.Response, attempts: int, started: float) -> GenerationResult:
        data = resp.json()
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("transient", "Malformed provider response (no choices).") from exc
        u = data.get("usage") or {}
        usage = Usage(u.get("prompt_tokens"), u.get("completion_tokens"))
        return GenerationResult(
            text=text,
            model=data.get("model", req.model),
            requested_model=req.model,
            usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            finish_reason=choice.get("finish_reason"),
            request_id=data.get("id") or resp.headers.get("x-request-id"),
            attempts=attempts,
        )

    # -------------------------------------------------------------- interface
    def generate(self, req: GenerationRequest) -> GenerationResult:
        started = time.monotonic()
        resp, attempts = self._post(self._payload(req), req.timeout_s)
        return self._to_result(req, resp, attempts, started)

    def structured_generate(self, req: GenerationRequest, schema: dict[str, Any], schema_name: str) -> GenerationResult:
        started = time.monotonic()
        total_attempts = 0
        resp = None
        # 1) OpenAI-standard json_schema; 2) Token Factory docs form; 3) json_object + schema in prompt.
        formats: list[dict[str, Any]] = [
            {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
            {"type": "json_schema", "json_schema": schema},
            {"type": "json_object"},
        ]
        last_err: ProviderError | None = None
        for fmt in formats:
            r = req
            if fmt["type"] == "json_object":
                hint = "Respond with ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
                r = GenerationRequest(req.model, [*req.messages, Message("system", hint)], req.temperature,
                                      req.max_tokens, req.timeout_s)
            try:
                resp, n = self._post(self._payload(r, response_format=fmt), req.timeout_s)
                total_attempts += n
                break
            except ProviderError as exc:
                total_attempts += 1
                last_err = exc
                if exc.kind == "permanent" and exc.status in (400, 422):
                    continue  # this response_format variant is not supported — try the next
                raise
        if resp is None:
            raise last_err or ProviderError("permanent", "No structured-output format accepted.")

        result = self._to_result(req, resp, total_attempts, started)
        try:
            parsed = extract_json(result.text)
            jsonschema.validate(parsed, schema)
        except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
            err = ProviderError("invalid_output", f"Model output violated schema '{schema_name}': {str(exc)[:300]}",
                                request_id=result.request_id)
            err.result = result  # type: ignore[attr-defined]  # keep usage so the failed call is still costed
            raise err from exc
        result.parsed = parsed
        return result

    def stream(self, req: GenerationRequest) -> Iterator[str]:
        self._require_key()
        body = self._payload(req, stream=True)
        with self._sem:
            try:
                with self._client.stream("POST", self._base + "chat/completions", headers=self._headers(),
                                         json=body, timeout=req.timeout_s) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise self._classify(resp)
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            return
                        try:
                            delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, json.JSONDecodeError):
                            continue
                        if delta:
                            yield delta
            except httpx.TimeoutException as exc:
                raise ProviderError("timeout", str(exc) or "stream timeout") from exc
            except httpx.TransportError as exc:
                raise ProviderError("transient", f"Network error: {type(exc).__name__}") from exc

    def count_tokens(self, messages: list[Message]) -> TokenCount:
        # Token Factory exposes no tokenizer endpoint; this is a conservative estimate used only for
        # pre-call budget checks. Real usage is always taken from the provider response.
        chars = sum(len(m.content) + 8 for m in messages)
        return TokenCount(count=max(1, chars // 3), basis="ESTIMATED")

    def list_models(self) -> list[str]:
        self._require_key()
        resp = self._client.get(self._base + "models", headers=self._headers(), timeout=20.0)
        if resp.status_code >= 400:
            raise self._classify(resp)
        return sorted(m["id"] for m in resp.json().get("data", []))

    def health_check(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            models = self.list_models()
            return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000), "model_count": len(models)}
        except ProviderError as exc:
            return {"ok": False, "kind": exc.kind, "message": exc.message,
                    "latency_ms": int((time.monotonic() - started) * 1000)}
