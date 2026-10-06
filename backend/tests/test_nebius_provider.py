import json
import os

import httpx
import pytest

from forge.providers import nebius as nebius_mod
from forge.providers.base import GenerationRequest, Message, ProviderError, Usage
from forge.providers.nebius import NebiusProvider, extract_json
from forge.providers.pricing import ModelPricing, compute_cost

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}
REQ = GenerationRequest("nvidia/test-model", [Message("user", "hi")])


def _provider(handler, **kw) -> NebiusProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NebiusProvider("test-key", "https://example.invalid/v1/", client=client, **kw)


def _ok(content: str, model="nvidia/test-model", usage=(10, 5)):
    return httpx.Response(200, json={
        "id": "req-1", "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    })


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(nebius_mod.time, "sleep", lambda s: None)


def test_generate_records_real_usage_and_auth_header():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _ok("hello")

    res = _provider(handler).generate(REQ)
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "nvidia/test-model"
    assert res.text == "hello" and res.usage.total_tokens == 15 and res.request_id == "req-1"


def test_missing_key_is_unavailable_not_a_network_call():
    p = NebiusProvider("", "https://example.invalid/v1/", client=httpx.Client(transport=httpx.MockTransport(lambda r: _ok("x"))))
    with pytest.raises(ProviderError) as e:
        p.generate(REQ)
    assert e.value.kind == "unavailable"


def test_rate_limit_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "slow down"}})
        return _ok("ok")

    res = _provider(handler).generate(REQ)
    assert res.attempts == 3 and calls["n"] == 3


def test_rate_limit_exhausted_raises_rate_limited():
    p = _provider(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}), max_retries=1)
    with pytest.raises(ProviderError) as e:
        p.generate(REQ)
    assert e.value.kind == "rate_limited" and e.value.retryable


def test_auth_error_is_permanent_and_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(ProviderError) as e:
        _provider(handler).generate(REQ)
    assert e.value.kind == "auth" and not e.value.retryable and calls["n"] == 1


def test_5xx_is_transient():
    p = _provider(lambda r: httpx.Response(503, text="down"), max_retries=0)
    with pytest.raises(ProviderError) as e:
        p.generate(REQ)
    assert e.value.kind == "transient" and e.value.retryable


def test_timeout_classified():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as e:
        _provider(handler, max_retries=0).generate(REQ)
    assert e.value.kind == "timeout"


def test_structured_valid():
    res = _provider(lambda r: _ok('{"n": 3}')).structured_generate(REQ, SCHEMA, "t")
    assert res.parsed == {"n": 3}


def test_structured_strips_think_and_fences():
    res = _provider(lambda r: _ok('<think>hmm</think>\n```json\n{"n": 7}\n```')).structured_generate(REQ, SCHEMA, "t")
    assert res.parsed == {"n": 7}


def test_structured_malformed_output_is_invalid_output_with_usage_retained():
    with pytest.raises(ProviderError) as e:
        _provider(lambda r: _ok("not json at all")).structured_generate(REQ, SCHEMA, "t")
    assert e.value.kind == "invalid_output" and not e.value.retryable
    assert e.value.result.usage.total_tokens == 15  # failed call still costed


def test_structured_schema_violation():
    with pytest.raises(ProviderError) as e:
        _provider(lambda r: _ok('{"n": "three"}')).structured_generate(REQ, SCHEMA, "t")
    assert e.value.kind == "invalid_output"


def test_structured_falls_back_when_response_format_unsupported():
    formats = []

    def handler(request):
        fmt = json.loads(request.content)["response_format"]["type"]
        formats.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})
        return _ok('{"n": 1}')

    res = _provider(handler).structured_generate(REQ, SCHEMA, "t")
    assert res.parsed == {"n": 1} and formats == ["json_schema", "json_schema", "json_object"]


def test_stream_yields_deltas():
    sse = ('data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
           'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
           "data: [DONE]\n\n")
    p = _provider(lambda r: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}))
    assert "".join(p.stream(REQ)) == "Hello"


def test_count_tokens_is_labelled_estimated():
    assert _provider(lambda r: _ok("x")).count_tokens([Message("user", "a" * 300)]).basis == "ESTIMATED"


def test_health_check_reports_failure_safely():
    h = _provider(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})).health_check()
    assert h["ok"] is False and h["kind"] == "auth"


def test_extract_json_garbage_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("nothing here")


def test_pricing_never_invents_prices():
    usage = Usage(1_000_000, 1_000_000)
    assert compute_cost("unknown", usage, pricing={}).basis == "UNAVAILABLE"
    table = {"m": ModelPricing(1.0, 2.0)}
    c = compute_cost("m", usage, pricing=table)
    assert c.usd == 3.0 and c.basis == "ACTUAL"
    assert compute_cost("m", usage, tokens_estimated=True, pricing=table).basis == "ESTIMATED"
    assert compute_cost("m", Usage(None, None), pricing=table).basis == "UNAVAILABLE"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEBIUS_API_KEY_LIVE"), reason="set NEBIUS_API_KEY_LIVE to run live Nebius tests")
def test_live_nebius_structured_roundtrip():
    p = NebiusProvider(os.environ["NEBIUS_API_KEY_LIVE"], os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"))
    model = os.environ.get("NEBIUS_MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")
    res = p.structured_generate(
        GenerationRequest(model, [Message("user", "Return n=42.")], max_tokens=300), SCHEMA, "live")
    assert res.parsed["n"] == 42 and res.usage.total_tokens
