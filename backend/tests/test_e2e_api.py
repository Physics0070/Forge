"""Full product flow through the real HTTP API + real worker. Scripted: the LLM and the two external HTTP services."""
import difflib
import io
import json
import re
import time
import zipfile

import httpx
import pytest

from forge.config import get_settings
from forge.engine.runtime import set_provider
from forge.engine.worker import Worker
from tests.conftest import auth_headers, signup
from tests.fakes import ScriptedProvider, final, tool

from tests.audit_script import (AKIA, APP, FILES, FIXED, GHSA, PATCH, audit_handler, finding, role, steps_done,  # noqa: F401
                                 upstream, zip_bytes)


def mock_http(monkeypatch):
    def handler(req: httpx.Request):
        if "osv.dev" in req.url.host:
            if req.url.path.endswith("querybatch"):
                return httpx.Response(200, json={"results": [{"vulns": [{"id": GHSA}]}]})
            return httpx.Response(200, json={"id": GHSA, "summary": "Prototype Pollution in lodash", "aliases": ["CVE-2020-8203"],
                                             "affected": [{"ranges": [{"events": [{"fixed": "4.17.21"}]}]}]})
        if "tavily" in req.url.host:
            assert req.headers["authorization"].startswith("Bearer tvly-")
            return httpx.Response(200, json={"results": [{"title": "pickle", "url": "https://docs.python.org/3/library/pickle.html",
                                                          "content": "Warning: The pickle module is not secure.", "score": 0.9}]})
        return httpx.Response(404)

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    from forge.tools.base import ToolContext

    monkeypatch.setattr(ToolContext, "client", lambda self: shared)
    monkeypatch.setattr(get_settings(), "tavily_api_key", "tvly-test-key")


def drain(provider, seconds=90):
    set_provider(provider)
    Worker(concurrency=4, poll_interval=0.05).run_until_idle(max_seconds=seconds, parallel=4)


def api(client, me):
    h = auth_headers(me)

    class A:
        def get(self, url, **kw):
            return client.get(url, **kw)

        def post(self, url, **kw):
            kw.setdefault("headers", {}).update(h)
            return client.post(url, **kw)

        def put(self, url, **kw):
            kw.setdefault("headers", {}).update(h)
            return client.put(url, **kw)

        def delete(self, url, **kw):
            kw.setdefault("headers", {}).update(h)
            return client.delete(url, **kw)

    return A()


@pytest.fixture
def flow(client, monkeypatch):
    mock_http(monkeypatch)
    me = signup(client)
    a = api(client, me)
    proj = a.post("/api/projects", json={"name": "Security Audit"}).json()
    repo = a.post(f"/api/projects/{proj['id']}/repositories/zip", files={"file": ("repo.zip", zip_bytes(), "application/zip")})
    assert repo.status_code == 201, repo.text
    wf = a.post("/api/workflows", json={"project_id": proj["id"], "template": "repository-security-audit",
                                        "goal": "Audit this repository for security issues and propose fixes"})
    assert wf.status_code == 201, wf.text
    return client, me, a, proj, repo.json(), wf.json()


def test_full_product_flow(flow):
    client, me, a, proj, repo, wf = flow
    assert repo["status"] == "ready" and repo["fileCount"] == 3 and repo["summary"]["extensions"][".py"] == 1
    assert wf["latestVersion"]["approved"] is False and wf["latestVersion"]["issues"] == []

    # an unapproved version cannot run
    r = a.post("/api/runs", json={"workflow_version_id": wf["latestVersion"]["id"], "repository_id": repo["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_APPROVED"

    ap = a.post(f"/api/workflows/{wf['id']}/versions/1/approve")
    assert ap.status_code == 200 and ap.json()["approved"] is True

    key = "run-key-1"
    body = {"workflow_version_id": wf["latestVersion"]["id"], "repository_id": repo["id"],
            "input": {"objective": "Audit this repository for security issues"}}
    run = a.post("/api/runs", json=body, headers={"Idempotency-Key": key})
    assert run.status_code == 201, run.text
    run = run.json()
    dup = a.post("/api/runs", json=body, headers={"Idempotency-Key": key})  # double-click / network retry
    assert dup.status_code == 200 and dup.json()["id"] == run["id"] and dup.json()["idempotentReplay"] is True
    assert len(a.get("/api/runs").json()["runs"]) == 1

    provider = ScriptedProvider(audit_handler)
    drain(provider)

    d = a.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "WAITING_APPROVAL", {n["id"]: n["state"] for n in d["nodes"]}
    st = {n["id"]: n["state"] for n in d["nodes"]}
    assert st["verify"] == "SUCCESS" and st["approve_fixes"] == "WAITING_APPROVAL" and st["apply_tests"] == "PENDING"
    assert st["recover_inputs"] == "SKIPPED" and st["any_fixes"] == "SUCCESS"
    assert len(d["pendingApprovals"]) == 1 and d["pendingApprovals"][0]["kind"] == "approve_patch"

    # --- verification is real: fabricated finding rejected, real ones verified
    v = {r["findingId"]: r for r in a.get(f"/api/runs/{run['id']}/verification").json()["results"]}
    assert v["SEC-1"]["status"] == "VERIFIED" and v["SEC-2"]["status"] == "VERIFIED" and v["DEP-1"]["status"] == "VERIFIED"
    assert v["SEC-3"]["status"] == "REJECTED" and "does not exist" in v["SEC-3"]["reason"]
    assert v["SEC-1"]["finding"]["title"] == "Unsafe pickle deserialization"

    # --- evidence artifacts persisted with provenance (Tavily + OSV), no raw text dumps
    ev = [x for x in a.get(f"/api/runs/{run['id']}/artifacts", params={"type": "evidence"}).json()["artifacts"]]
    srcs = {a.get("provenance", {}).get("tool") for a in ev}
    assert {"tavily", "osv.dev"} <= srcs
    full = a.get(f"/api/artifacts/{ev[0]['id']}").json()
    assert {"source", "url", "query", "timestamp", "snippet", "agent"} <= set(full["content"]) and full["contentHash"]

    # --- grant the first approval; the patch is applied ONLY in the isolated copy
    appr = d["pendingApprovals"][0]
    assert appr["request"]["payload"]["proposals"][0]["finding_id"] == "SEC-1"
    r = a.post(f"/api/approvals/{appr['id']}", json={"decision": "grant", "note": "ship it"})
    assert r.status_code == 200 and r.json()["status"] == "GRANTED"
    assert a.post(f"/api/approvals/{appr['id']}", json={"decision": "grant"}).status_code == 409  # decide once
    drain(provider)
    d = a.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "WAITING_APPROVAL" and [p["kind"] for p in d["pendingApprovals"]] == ["approve_final_diff"]
    node = a.get(f"/api/runs/{run['id']}/nodes/apply_tests").json()
    assert node["output"]["tests"]["status"] == "NOT_AVAILABLE"  # honest: sandbox not available here
    assert "json.loads(blob)" in node["output"]["diff"] and "AGENT-CLAIMED" not in node["output"]["diff"]  # real diff, not the claim
    assert any(t["tool"] == "RepositoryWrite" and t["decision"] == "ALLOW" for t in node["toolCalls"])

    # --- second approval -> report
    a.post(f"/api/approvals/{d['pendingApprovals'][0]['id']}", json={"decision": "grant"})
    drain(provider)
    d = a.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "SUCCESS", {n["id"]: (n["state"], n["error"]) for n in d["nodes"]}
    assert all(n["state"] in ("SUCCESS", "SKIPPED") for n in d["nodes"])
    t = d["totals"]
    assert t["modelCalls"] == len(provider.calls) and t["totalTokens"] == 150 * len(provider.calls)
    assert t["costBasis"] == "UNAVAILABLE" and t["costUsd"] is None  # no price configured: never invented
    assert t["policyViolations"] == 0 and d["durationS"] is not None

    # --- exports
    md = a.get(f"/api/runs/{run['id']}/export", params={"format": "markdown"})
    assert md.status_code == 200 and md.text.startswith("# Security report")
    exp = a.get(f"/api/runs/{run['id']}/export", params={"format": "json"}).json()
    assert exp["format"] == "forge.run/1" and exp["workflow"]["nodes"] and exp["reproducibility"]["workflowVersionId"] == run["workflowVersionId"]
    assert any(x["type"] == "patch" for x in exp["artifacts"])
    z = a.get(f"/api/runs/{run['id']}/export", params={"format": "bundle"})
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist()[0] == "run.json"
    tr = a.get(f"/api/runs/{run['id']}/export", params={"format": "trace"}).json()
    types = [e["type"] for e in tr["events"]]
    for needed in ("RUN_CREATED", "NODE_READY", "NODE_STARTED", "MODEL_CALLED", "TOOL_CALLED", "ARTIFACT_CREATED", "HANDOFF_VALIDATED",
                   "VERIFICATION_STARTED", "VERIFICATION_PASSED", "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "NODE_SUCCEEDED",
                   "WORKFLOW_COMPLETED"):
        assert needed in types, needed

    # --- replay: new run, original untouched
    before = a.get(f"/api/runs/{run['id']}").json()
    rp = a.post(f"/api/runs/{run['id']}/replay", json={"mode": "different_model", "model": "test-ultra",
                                                      "reuse_nodes": ["planner", "repo_scanner"]})
    assert rp.status_code == 201, rp.text
    rp = rp.json()
    assert rp["id"] != run["id"] and rp["parentRunId"] == run["id"] and rp["replayConfig"]["model"] == "test-ultra"
    assert {n["id"]: n for n in rp["nodes"]}["planner"]["reusedFromRun"] == run["id"]
    assert a.get(f"/api/runs/{run['id']}").json() == before

    # --- compare
    cmp = a.get("/api/runs/compare", params={"a": run["id"], "b": rp["id"]}).json()
    assert cmp["sameInput"] and cmp["sameWorkflowVersion"] and cmp["diff"]["success"]["differs"] is True

    # --- dashboard shows only real numbers
    dash = a.get("/api/dashboard").json()
    assert dash["totalRuns"] == 2 and dash["completedRuns"] == 1 and dash["verifiedFindings"] == 3
    assert dash["totalCostUsd"] is None and dash["costBasis"] == "UNAVAILABLE"


def test_sse_stream_replays_and_terminates(flow):
    client, me, a, proj, repo, wf = flow
    a.post(f"/api/workflows/{wf['id']}/versions/1/approve")
    run = a.post("/api/runs", json={"workflow_version_id": wf["latestVersion"]["id"], "repository_id": repo["id"]}).json()
    # a tiny cancelled run is enough to exercise the stream protocol
    a.post(f"/api/runs/{run['id']}/cancel")
    got, ended = [], False
    with client.stream("GET", f"/api/runs/{run['id']}/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: ") and "metadata" in line:
                got.append(json.loads(line[6:]))
            if line.startswith("event: end"):
                ended = True
                break
    assert ended and got[0]["type"] == "RUN_CREATED" and got[-1]["type"] == "WORKFLOW_COMPLETED"
    ids = [e["id"] for e in got]
    assert ids == sorted(ids)


def test_run_rejected_when_approval_denied_ends_failed_cleanly(flow):
    client, me, a, proj, repo, wf = flow
    a.post(f"/api/workflows/{wf['id']}/versions/1/approve")
    run = a.post("/api/runs", json={"workflow_version_id": wf["latestVersion"]["id"], "repository_id": repo["id"]}).json()
    drain(ScriptedProvider(audit_handler))
    d = a.get(f"/api/runs/{run['id']}").json()
    r = a.post(f"/api/approvals/{d['pendingApprovals'][0]['id']}", json={"decision": "reject", "note": "no thanks"})
    assert r.json()["status"] == "REJECTED"
    drain(ScriptedProvider(audit_handler))
    d = a.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "SUCCESS"  # rejecting remediation skips it; the report is still produced from verified results
    st = {n["id"]: n["state"] for n in d["nodes"]}
    assert st["approve_fixes"] == "SKIPPED" and st["apply_tests"] == "SKIPPED" and st["approve_final"] == "SKIPPED" and st["report"] == "SUCCESS"
    assert not [x for x in a.get(f"/api/runs/{run['id']}/artifacts", params={"type": "applied_patch"}).json()["artifacts"]]


def test_prompt_injection_in_repo_never_changes_workspace(flow):
    """README says 'ignore previous instructions...'. Scanner reads it; nothing leaks; no write happens before approval."""
    client, me, a, proj, repo, wf = flow
    a.post(f"/api/workflows/{wf['id']}/versions/1/approve")
    run = a.post("/api/runs", json={"workflow_version_id": wf["latestVersion"]["id"], "repository_id": repo["id"]}).json()

    def hijacked(req, schema, i):
        if role(req) == "Repository Scanner" and steps_done(req) == 0:
            return tool("RepositoryRead", path="README.md")
        if role(req) == "Repository Scanner" and steps_done(req) == 1:  # a fooled model tries to obey the README
            return tool("RepositoryWrite", patch=PATCH)
        return audit_handler(req, schema, i)

    drain(ScriptedProvider(hijacked))
    d = a.get(f"/api/runs/{run['id']}").json()
    scan = {n["id"]: n for n in d["nodes"]}["repo_scanner"]
    assert scan["state"] == "FAILED" and scan["error"]["class"] == "policy_violation"
    assert d["totals"]["policyViolations"] >= 1
    evs = a.get(f"/api/runs/{run['id']}/events", params={"type": "POLICY_BLOCKED"}).json()["events"]
    assert evs and "No changes were made" in evs[0]["metadata"]["message"]
    assert a.get(f"/api/runs/{run['id']}/artifacts", params={"type": "applied_patch"}).json()["artifacts"] == []
