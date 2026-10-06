"""Serverless-mode pieces: pure-Python patching, tarball GitHub import, time-sliced execution, tick endpoint."""
import io
import tarfile
import time

import httpx
import pytest

from forge import repos
from forge.config import get_settings
from forge.patching import PatchError, apply_patch, init_baseline, workspace_diff
from forge.sandbox import SandboxError, safe_resolve
from tests.engine_helpers import Env, agent_node, ir, nodes_of, run_row, tool_calls, work
from tests.fakes import ScriptedProvider, final, tool


# ------------------------------------------------------------------ patching
@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "run" / "repo"
    r.mkdir(parents=True)
    (r / "a.py").write_bytes(b"one\r\ntwo\r\nthree\r\n")  # CRLF file
    (r / "b.txt").write_text("alpha\nbeta\n")
    init_baseline(r)
    return r


def res(repo):
    return lambda rel: safe_resolve(repo, rel)


def test_apply_multi_file_patch_preserves_crlf_and_diffs(repo):
    patch = ("--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
             "--- a/b.txt\n+++ b/b.txt\n@@ -1,2 +1,3 @@\n alpha\n beta\n+gamma\n")
    assert apply_patch(repo, patch, res(repo)) == ["a.py", "b.txt"]
    assert (repo / "a.py").read_bytes() == b"one\r\nTWO\r\nthree\r\n"
    assert (repo / "b.txt").read_text() == "alpha\nbeta\ngamma\n"
    d = workspace_diff(repo)
    assert "-two" in d and "+TWO" in d and "+gamma" in d and d.count("diff --git") == 2


def test_patch_is_atomic(repo):
    patch = ("--- a/b.txt\n+++ b/b.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+BETA\n"
             "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-nope\n+x\n")
    with pytest.raises(PatchError) as e:
        apply_patch(repo, patch, res(repo))
    assert e.value.code == "patch_does_not_apply"
    assert (repo / "b.txt").read_text() == "alpha\nbeta\n" and workspace_diff(repo) == ""  # nothing written


def test_new_and_deleted_files_and_fuzz(repo):
    patch = ("--- /dev/null\n+++ b/new/c.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
             "--- a/b.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-alpha\n-beta\n"
             "--- a/a.py\n+++ b/a.py\n@@ -40,1 +40,1 @@\n-three\n+3\n")  # wrong line number, found by fuzz
    apply_patch(repo, patch, res(repo))
    assert (repo / "new" / "c.txt").read_text() == "hello\nworld\n" and not (repo / "b.txt").exists()
    assert (repo / "a.py").read_bytes().endswith(b"3\r\n")
    d = workspace_diff(repo)
    assert "+++ /dev/null" in d and "--- /dev/null" in d


def test_patch_paths_are_sandboxed(repo):
    with pytest.raises(SandboxError):
        apply_patch(repo, "--- a/../x\n+++ b/../x\n@@ -0,0 +1 @@\n+x\n", res(repo))
    with pytest.raises(PatchError):
        apply_patch(repo, "just text", res(repo))


# ------------------------------------------------------------- tarball import
def _tarball(files: dict[str, bytes], top="psf-requests-abc1234") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(f"{top}/{name}")
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_github_tarball_import_follows_only_codeload(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.host == "api.github.com":
            assert req.headers.get("authorization") == "Bearer ghp_tok"
            return httpx.Response(302, headers={"location": "https://codeload.github.com/psf/requests/legacy.tar.gz/main"})
        assert "authorization" not in req.headers  # token never forwarded to the redirect target
        return httpx.Response(200, content=_tarball({"a.py": b"x=1", "lib/b.py": b"y=2"}))

    snap = repos.import_github("https://github.com/psf/requests", "main", "ghp_tok", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert snap.file_count == 2 and snap.commit_sha == "abc1234"
    assert seen[0] == "https://api.github.com/repos/psf/requests/tarball/main"


def test_github_redirect_to_other_host_refused():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data"})

    with pytest.raises(repos.RepoImportError, match="Unexpected redirect"):
        repos.import_github("https://github.com/a/b", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_github_not_found_message():
    with pytest.raises(repos.RepoImportError, match="not found"):
        repos.import_github("https://github.com/a/b", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))


# -------------------------------------------------------- time-sliced execution
def test_agent_suspends_at_slice_end_and_resumes_from_checkpoint(monkeypatch):
    env = Env()
    env.add_repo({"src/app.py": b"import pickle\n"})
    rid = env.start(ir([agent_node("s", "repository_scanner", tools=["CodeSearch"], permissions=["repository.read"])]), repo=True)
    calls = {"n": 0}

    from forge.engine.runtime import set_provider
    from forge.engine.worker import Worker

    w = Worker(concurrency=1, poll_interval=0.05)

    def handler(req, schema, i):
        calls["n"] += 1  # shared across slices (each slice gets its own provider instance)
        if calls["n"] == 1:
            return tool("CodeSearch", pattern="pickle")
        return final({"ok": True})

    def first_slice(req, schema, i):
        out = handler(req, schema, i)
        for c in list(_ctxs):  # the slice's time runs out while the model is thinking
            c.yield_at = time.monotonic() - 1
        return out

    import forge.engine.agent_runner as ar

    _ctxs = []
    orig = ar.pick_model
    monkeypatch.setattr(ar, "pick_model", lambda ctx: (_ctxs.append(ctx), orig(ctx))[1])
    set_provider(ScriptedProvider(first_slice))
    w.yield_at = time.monotonic() + 60
    w.run_until_idle(max_seconds=10, parallel=1)
    monkeypatch.setattr(ar, "pick_model", orig)
    from tests.engine_helpers import events_of

    # suspended mid-flight, requeued, resumed from the checkpoint: the tool ran once, the model twice
    assert any(e.type == "NODE_SUSPENDED" for e in events_of(rid))
    assert run_row(rid).status == "SUCCESS" and calls["n"] == 2
    assert [t.tool for t in tool_calls(rid)] == ["CodeSearch"] and nodes_of(rid)["s"].attempt == 1


def test_tick_endpoint_requires_secret(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", "s3cret-value")
    assert client.post("/api/internal/tick").status_code == 404
    assert client.post("/api/internal/tick", headers={"x-forge-cron-secret": "wrong"}).status_code == 404
    r = client.post("/api/internal/tick", headers={"x-forge-cron-secret": "s3cret-value"})
    assert r.status_code == 200 and r.json()["processed"] == 0
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert client.post("/api/internal/tick", headers={"x-forge-cron-secret": ""}).status_code == 404


def test_pump_only_in_serverless_mode(client, monkeypatch):
    from tests.conftest import auth_headers, signup

    me = signup(client)
    import uuid

    assert client.post(f"/api/runs/{uuid.uuid4()}/pump", headers=auth_headers(me)).status_code == 404
