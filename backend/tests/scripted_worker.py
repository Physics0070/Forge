"""TEST HARNESS ONLY - a real FORGE worker whose model provider is the scripted test provider.

Used to exercise the UI end-to-end without a Nebius API key. It lives in tests/ (never imported by the app),
refuses to start when FORGE_ENV=production, and every run it executes is visibly the scripted security
audit from tests/test_e2e_api.py. Production workers are started with `python -m forge.engine.worker`.

    python -m tests.scripted_worker
"""
from __future__ import annotations

import os
import sys
import time

if os.environ.get("FORGE_ENV", "development") == "production":
    sys.exit("scripted_worker is a test harness and must never run in production.")

import httpx  # noqa: E402

from forge.engine.runtime import set_provider  # noqa: E402
from forge.engine.worker import Worker  # noqa: E402
from forge.logging import setup_logging  # noqa: E402
from forge.tools.base import ToolContext  # noqa: E402
from tests.fakes import ScriptedProvider  # noqa: E402
from tests.audit_script import GHSA, audit_handler  # noqa: E402


def _http(req: httpx.Request) -> httpx.Response:
    if "osv.dev" in req.url.host:
        if req.url.path.endswith("querybatch"):
            return httpx.Response(200, json={"results": [{"vulns": [{"id": GHSA}]}]})
        return httpx.Response(200, json={"id": GHSA, "summary": "Prototype Pollution in lodash", "aliases": ["CVE-2020-8203"],
                                         "affected": [{"ranges": [{"events": [{"fixed": "4.17.21"}]}]}]})
    if "tavily" in req.url.host:
        return httpx.Response(200, json={"results": [{"title": "pickle", "url": "https://docs.python.org/3/library/pickle.html",
                                                      "content": "Warning: The pickle module is not secure.", "score": 0.9}]})
    return httpx.Response(404)


def main() -> None:
    setup_logging("scripted-worker")
    shared = httpx.Client(transport=httpx.MockTransport(_http))
    ToolContext.client = lambda self: shared  # type: ignore[method-assign]
    from forge.config import get_settings

    get_settings().tavily_api_key = "tvly-scripted"
    delay = float(os.environ.get("SCRIPTED_LATENCY_S", "0.8"))  # makes the live view observable

    def slow(req, schema, i):
        time.sleep(delay)
        return audit_handler(req, schema, i)

    set_provider(ScriptedProvider(slow))
    Worker(worker_id="scripted-harness", poll_interval=0.3).run_forever()


if __name__ == "__main__":
    main()
