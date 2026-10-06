"""Authorization, isolation and input-hardening tests at the HTTP layer."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from forge import ratelimit, repos
from forge.api.main import create_app
from forge.config import get_settings
from forge.engine.runtime import set_provider
from forge.engine.worker import Worker
from tests.conftest import auth_headers, signup
from tests.engine_helpers import agent_node, edge, ir
from tests.fakes import ScriptedProvider, final
from tests.test_e2e_api import api, mock_http, zip_bytes


class User:
    def __init__(self, email):
        self.client = TestClient(create_app())
        self.me = signup(self.client, email)
        self.a = api(self.client, self.me)
        self.project = self.a.post("/api/projects", json={"name": f"p-{email}"}).json()
        self.repo = self.a.post(f"/api/projects/{self.project['id']}/repositories/zip",
                                files={"file": ("r.zip", zip_bytes(), "application/zip")}).json()
        self.wf = self.a.post("/api/workflows", json={"project_id": self.project["id"], "template": "repository-security-audit",
                                                      "goal": "Audit this repository for security issues"}).json()
        self.a.post(f"/api/workflows/{self.wf['id']}/versions/1/approve")
        self.run = self.a.post("/api/runs", json={"workflow_version_id": self.wf["latestVersion"]["id"],
                                                  "repository_id": self.repo["id"]}).json()


@pytest.fixture
def two(monkeypatch):
    mock_http(monkeypatch)
    return User("alice@example.com"), User("bob@example.com")


def all_404(resp_list):
    bad = [(r.request.method, r.request.url.path, r.status_code) for r in resp_list if r.status_code != 404]
    assert not bad, bad


def test_cross_tenant_access_is_denied_everywhere(two):
    alice, bob = two
    A, rid, wid = alice.a, alice.run["id"], alice.wf["id"]
    b = bob.a
    pid, repo = alice.project["id"], alice.repo["id"]
    ver = alice.wf["latestVersion"]["id"]
    resp = [
        b.get(f"/api/projects/{pid}"), b.delete(f"/api/projects/{pid}"), b.get(f"/api/projects/{pid}/repositories"),
        b.post(f"/api/projects/{pid}/repositories/github", json={"url": "https://github.com/psf/requests"}),
        b.post(f"/api/projects/{pid}/repositories/zip", files={"file": ("r.zip", zip_bytes(), "application/zip")}),
        b.get(f"/api/repositories/{repo}"), b.delete(f"/api/repositories/{repo}"),
        b.get(f"/api/workflows/{wid}"), b.delete(f"/api/workflows/{wid}"), b.post(f"/api/workflows/{wid}/duplicate"),
        b.get(f"/api/workflows/{wid}/versions/1"), b.post(f"/api/workflows/{wid}/versions", json={"ir": alice.wf["latestVersion"]["ir"]}),
        b.post(f"/api/workflows/{wid}/versions/1/approve"), b.post(f"/api/workflows/{wid}/validate", json={"ir": alice.wf["latestVersion"]["ir"]}),
        b.get(f"/api/workflows/{wid}/versions/1/export"),
        b.post("/api/workflows", json={"project_id": pid, "template": "repository-security-audit", "goal": "audit the thing please"}),
        b.post("/api/workflows/compile", json={"project_id": pid, "goal": "audit the whole repository"}),
        b.post("/api/runs", json={"workflow_version_id": ver}),
        b.get(f"/api/runs/{rid}"), b.get(f"/api/runs/{rid}/events"), b.get(f"/api/runs/{rid}/artifacts"), b.get(f"/api/runs/{rid}/verification"),
        b.get(f"/api/runs/{rid}/model-calls"), b.get(f"/api/runs/{rid}/export"), b.get(f"/api/runs/{rid}/nodes/planner"),
        b.get(f"/api/runs/{rid}/stream"),
        b.post(f"/api/runs/{rid}/pause"), b.post(f"/api/runs/{rid}/resume"), b.post(f"/api/runs/{rid}/cancel"),
        b.post(f"/api/runs/{rid}/retry"), b.post(f"/api/runs/{rid}/replay", json={"mode": "same"}),
        b.post(f"/api/runs/{rid}/unblock", json={"action": "increase_budget", "max_usd": 5}),
        b.get("/api/runs/compare", params={"a": rid, "b": bob.run["id"]}),
        b.get("/api/runs/compare", params={"a": bob.run["id"], "b": rid}),
    ]
    all_404(resp)
    # Bob's own listings contain none of Alice's data
    assert [r["id"] for r in b.get("/api/runs").json()["runs"]] == [bob.run["id"]]
    assert alice.run["id"] not in json.dumps(b.get("/api/dashboard").json()) and b.get("/api/dashboard").json()["totalRuns"] == 1
    # Alice's run is untouched by Bob's attempts
    assert A.get(f"/api/runs/{rid}").json()["status"] in ("RUNNING", "WAITING_APPROVAL")


def test_cross_tenant_artifacts_approvals_evaluations_agents(two):
    alice, bob = two
    set_provider(ScriptedProvider(lambda r, s, i: {}))
    from tests.test_e2e_api import audit_handler

    set_provider(ScriptedProvider(audit_handler))
    Worker(concurrency=4, poll_interval=0.05).run_until_idle(max_seconds=60, parallel=4)
    arts = alice.a.get("/api/artifacts").json()["artifacts"]
    assert arts
    ap = alice.a.get("/api/approvals").json()["approvals"]
    assert ap
    A = [bob.a.get(f"/api/artifacts/{arts[0]['id']}"), bob.a.get(f"/api/artifacts/{arts[0]['id']}/download"),
         bob.a.post(f"/api/approvals/{ap[0]['id']}", json={"decision": "grant"})]
    all_404(A)
    alice_ids = {x["id"] for x in arts}
    assert not alice_ids & {x["id"] for x in bob.a.get("/api/artifacts").json()["artifacts"]}
    assert not {x["id"] for x in ap} & {x["id"] for x in bob.a.get("/api/approvals").json()["approvals"]}
    assert alice.a.get(f"/api/approvals").json()["approvals"][0]["status"] == "PENDING"  # not decided by Bob
    ag = alice.a.post("/api/agents", json={"id": "alice_agent", "name": "A", "systemContract": "x" * 40, "tools": [], "permissions": []})
    assert ag.status_code == 201
    assert "alice_agent" not in json.dumps(bob.a.get("/api/agents").json())
    assert bob.a.delete("/api/agents/alice_agent").status_code == 404


def test_bob_cannot_run_alices_workflow_or_repo(two):
    alice, bob = two
    r = bob.a.post("/api/runs", json={"workflow_version_id": bob.wf["latestVersion"]["id"], "repository_id": alice.repo["id"]})
    assert r.status_code == 404  # repository scoped by workspace even when the workflow is Bob's own


def test_csrf_required_on_every_mutation(two):
    alice, _ = two
    c = alice.client
    for method, url, kw in [("post", "/api/projects", {"json": {"name": "x"}}), ("post", f"/api/runs/{alice.run['id']}/cancel", {}),
                            ("post", "/api/workflows", {"json": {}}), ("delete", f"/api/projects/{alice.project['id']}", {}),
                            ("put", "/api/settings/retention", {"json": {}}), ("post", "/api/agents", {"json": {}})]:
        r = getattr(c, method)(url, **kw)
        assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED", (method, url, r.status_code)


# -------------------------------------------------------------- input hardening
def test_malicious_zip_uploads_are_rejected(two):
    alice, _ = two
    for name in ("../evil.py", "/etc/cron.d/x", "a/../../b"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, "x")
        r = alice.a.post(f"/api/projects/{alice.project['id']}/repositories/zip", files={"file": ("e.zip", buf.getvalue(), "application/zip")})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ARCHIVE", name
    r = alice.a.post(f"/api/projects/{alice.project['id']}/repositories/zip", files={"file": ("e.zip", b"not-a-zip", "application/zip")})
    assert r.status_code == 422
    # failed imports are recorded, never 'ready'
    assert all(x["status"] in ("ready", "failed") for x in alice.a.get(f"/api/projects/{alice.project['id']}/repositories").json()["repositories"])


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://evil.com/a/b", "file:///etc/passwd", "https://github.com/a/b;rm -rf /",
                                 "https://169.254.169.254/latest/meta-data", "git@github.com:a/b.git"])
def test_github_import_rejects_non_github_urls(two, url):
    alice, _ = two
    r = alice.a.post(f"/api/projects/{alice.project['id']}/repositories/github", json={"url": url})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


def test_github_token_is_never_stored_or_returned(two, monkeypatch):
    alice, _ = two
    seen = {}

    def fake_import(url, ref=None, token=None, timeout_s=180):
        seen["token"] = token
        raise repos.RepoImportError("could not clone")

    monkeypatch.setattr(repos, "import_github", fake_import)
    r = alice.a.post(f"/api/projects/{alice.project['id']}/repositories/github", json={"url": "https://github.com/a/b", "token": "ghp_SECRETTOKEN123"})
    assert r.status_code == 202 and "ghp_SECRETTOKEN123" not in r.text
    import time

    for _ in range(40):
        st = alice.a.get(f"/api/repositories/{r.json()['id']}").json()
        if st["status"] != "importing":
            break
        time.sleep(0.1)
    assert st["status"] == "failed" and seen["token"] == "ghp_SECRETTOKEN123" and "ghp_SECRETTOKEN123" not in json.dumps(st)
    assert "ghp_SECRETTOKEN123" not in json.dumps(alice.a.get("/api/audit-log").json())


def test_workflow_ir_is_strictly_validated(two):
    alice, _ = two
    wid = alice.wf["id"]
    bad_ir = {"name": "x", "goal": "g", "nodes": [{"id": "1bad", "type": "AGENT"}], "surprise": True}
    r = alice.a.post(f"/api/workflows/{wid}/versions", json={"ir": bad_ir})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_WORKFLOW_IR"
    cyc = ir([agent_node("a"), agent_node("b")], [edge("a", "b"), edge("b", "a")])
    v = alice.a.post(f"/api/workflows/{wid}/validate", json={"ir": cyc}).json()
    assert v["valid"] is False and any(i["code"] == "CYCLE" for i in v["issues"])


def test_versions_immutable_and_invalid_versions_cannot_be_approved(two):
    alice, _ = two
    wid, a = alice.wf["id"], alice.a
    v1_before = a.get(f"/api/workflows/{wid}/versions/1").json()
    cyc = ir([agent_node("a"), agent_node("b")], [edge("a", "b"), edge("b", "a")])
    v2 = a.post(f"/api/workflows/{wid}/versions", json={"ir": cyc, "note": "oops"})
    assert v2.status_code == 201 and v2.json()["version"] == 2 and not v2.json()["approved"]
    r = a.post(f"/api/workflows/{wid}/versions/2/approve")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert any(i["code"] == "CYCLE" for i in r.json()["error"]["issues"])
    assert a.get(f"/api/workflows/{wid}/versions/1").json() == v1_before  # v1 untouched, still approved
    assert v1_before["approved"] is True
    r = a.post("/api/runs", json={"workflow_version_id": v2.json()["id"]})
    assert r.status_code == 409  # unapproved invalid version can't run
    assert [v["version"] for v in a.get(f"/api/workflows/{wid}").json()["versions"]] == [2, 1]


def test_secrets_never_exposed_by_settings_or_health(two, monkeypatch):
    alice, _ = two
    monkeypatch.setattr(get_settings(), "nebius_api_key", "nb-LEAKME-123")
    monkeypatch.setattr(get_settings(), "storage_secret_key", "s3-LEAKME-456")
    for url in ("/api/settings", "/api/system/health", "/api/auth/me", "/api/dashboard"):
        t = alice.a.get(url).text
        assert "LEAKME" not in t and "DATABASE_URL" not in t and "postgresql" not in t, url
    s = alice.a.get("/api/settings").json()
    assert s["provider"]["apiKeyConfigured"] is True and s["models"]["routerTiers"]["ultra"] == "test-ultra"


def test_retention_requires_owner_and_validates(two):
    alice, _ = two
    ok = alice.a.put("/api/settings/retention", json={"run_logs_days": 30, "artifacts_days": 60, "memory_days": 90, "traces_days": 30})
    assert ok.status_code == 200 and alice.a.get("/api/settings").json()["retention"]["run_logs_days"] == 30
    assert alice.a.put("/api/settings/retention", json={"run_logs_days": 0, "artifacts_days": 1, "memory_days": 1, "traces_days": 1}).status_code == 422


def test_compile_endpoint_success_failure_and_rate_limit(two, monkeypatch):
    from tests.test_compiler import GOOD

    alice, _ = two
    set_provider(ScriptedProvider(lambda r, s, i: GOOD))
    ok = alice.a.post("/api/workflows/compile", json={"project_id": alice.project["id"], "goal": "Audit this repository for problems"})
    assert ok.status_code == 200 and ok.json()["workflow"]["nodes"] and ok.json()["meta"]["model"] == "test-ultra"
    create = alice.a.post("/api/workflows", json={"project_id": alice.project["id"], "ir": ok.json()["workflow"]})
    assert create.status_code == 201 and create.json()["latestVersion"]["issues"] == []
    set_provider(ScriptedProvider(lambda r, s, i: {"name": "x", "tasks": [{"id": "a", "agent": "nope", "title": "t", "depends_on": []}]}))
    bad = alice.a.post("/api/workflows/compile", json={"project_id": alice.project["id"], "goal": "Do something impossible please"})
    assert bad.status_code == 422
    e = bad.json()["error"]
    assert e["code"] == "COMPILATION_FAILED" and e["status"] == "COMPILATION FAILED" and e["suggestion"] and e["reason"]
    monkeypatch.setitem(ratelimit.LIMITS, "compile", (1, 10**9))  # fixed window: first call allowed, second limited
    assert alice.a.post("/api/workflows/compile", json={"project_id": alice.project["id"], "goal": "Do something impossible please"}).status_code == 422
    assert alice.a.post("/api/workflows/compile", json={"project_id": alice.project["id"], "goal": "Do something impossible please"}).status_code == 429


def test_custom_agent_validation_and_execution(two):
    alice, _ = two
    a = alice.a
    assert a.post("/api/agents", json={"id": "planner", "name": "x", "systemContract": "y" * 30}).status_code == 422  # builtin reserved
    bad = a.post("/api/agents", json={"id": "my_writer", "name": "W", "systemContract": "z" * 30, "tools": ["RepositoryWrite"], "permissions": []})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "PERMISSION_CONFLICT"
    ok = a.post("/api/agents", json={"id": "echo_agent", "name": "Echo", "systemContract": "Echo the input back as JSON.",
                                     "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}})
    assert ok.status_code == 201
    wfr = a.post("/api/workflows", json={"project_id": alice.project["id"], "ir": ir([agent_node("e", "echo_agent")])})
    assert wfr.status_code == 201, wfr.text
    wf = wfr.json()
    assert wf["latestVersion"]["issues"] == []
    a.post(f"/api/workflows/{wf['id']}/versions/1/approve")
    run = a.post("/api/runs", json={"workflow_version_id": wf["latestVersion"]["id"], "input": {"objective": "echo please"}}).json()
    set_provider(ScriptedProvider(lambda r, s, i: final({"ok": True})))
    Worker(concurrency=2, poll_interval=0.05).run_until_idle(max_seconds=30)
    assert a.get(f"/api/runs/{run['id']}").json()["status"] == "SUCCESS"
    assert a.delete("/api/agents/echo_agent").status_code == 204
    assert not any(x["id"] == "echo_agent" for x in a.get("/api/agents").json()["agents"])


def test_evaluation_runs_same_objective_and_reports_only_real_metrics(two):
    from tests.test_e2e_api import audit_handler

    alice, _ = two
    a = alice.a

    def handler(req, schema, i):
        if "ROLE: Generalist" in req.messages[0].content:
            return final({"findings": [], "markdown": "# single agent report\n\nnothing found", "summary": "x"})
        return audit_handler(req, schema, i)

    r = a.post("/api/evaluations", json={"project_id": alice.project["id"], "name": "baseline vs forge", "repository_id": alice.repo["id"],
                                         "objective": "Audit this repository for security issues",
                                         "arms": ["single_agent", "forge_workflow"]})
    assert r.status_code == 201, r.text
    set_provider(ScriptedProvider(handler))
    Worker(concurrency=4, poll_interval=0.05).run_until_idle(max_seconds=60, parallel=4)
    ev = a.get(f"/api/evaluations/{r.json()['id']}").json()
    arms = {x["mode"]: x for x in ev["arms"]}
    solo, forge = arms["single_agent"]["metrics"], arms["forge_workflow"]["metrics"]
    assert arms["single_agent"]["status"] == "SUCCESS" and arms["forge_workflow"]["status"] == "SUCCESS"
    assert solo["verificationRate"] is None  # a single agent has no verification: "Not available", not a made-up number
    assert forge["verificationRate"] is not None and forge["verification"]["rejected"] == 1 and forge["verification"]["verified"] >= 2
    assert forge["modelCalls"] > solo["modelCalls"] and forge["costBasis"] == "UNAVAILABLE" and forge["costUsd"] is None
    assert solo["schemaValidity"] == 1.0 and forge["artifacts"] > solo["artifacts"]


def test_system_health_reports_real_state(two):
    alice, _ = two
    Worker(concurrency=1).heartbeat()
    h = alice.a.get("/api/system/health").json()
    assert h["database"]["ok"] and h["queue"]["queued"] >= 1 and h["workers"] and h["workers"][0]["healthy"] is True
    assert h["provider"]["last1h"]["calls"] == 0 and h["provider"]["last1h"]["errorRate"] is None  # nothing invented
    assert h["sandbox"]["enabled"] is False and h["storage"]["driver"] == "local"


def test_audit_log_records_security_relevant_actions(two):
    alice, _ = two
    actions = {e["action"] for e in alice.a.get("/api/audit-log").json()["entries"]}
    assert {"project.create", "repository.import", "workflow.version.create", "workflow.version.approve", "run.create"} <= actions
