from pathlib import Path

import pytest

from forge.verification import evidence_in_window, verify_findings
from forge.workflow.ir import VerificationPolicy

SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"
POL = VerificationPolicy()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "app.py").write_text(
        f"import pickle\nAWS = '{SECRET}'\ndef f(x):\n    return pickle.loads(x)\n", newline="\n")
    (r / "package.json").write_text('{"dependencies": {"lodash": "4.17.15"}}')
    return r


def finding(**kw):
    base = {"id": "SEC-1", "title": "Unsafe deserialization", "severity": "high", "category": "insecure_api",
            "file": "src/app.py", "line": 4, "description": "pickle.loads on input", "evidence": "return pickle.loads(x)",
            "confidence": 0.8, "remediation": "use json", "references": []}
    base.update(kw)
    return base


TRACE = [{"tool": "RepositoryRead", "input": {"path": "src/app.py"}, "output_summary": {}}]
EV = [{"source": "tavily", "url": "https://example.org/pickle", "query": "pickle", "timestamp": "t", "snippet": "s", "agent": "r",
       "finding_ids": ["SEC-1"]}]


def run(findings, repo, evidence=(), trace=TRACE, judge=None):
    return verify_findings(findings, repo_dir=repo, evidence=list(evidence), tool_calls=trace, policy=POL, judge=judge)


def status(out, fid="SEC-1"):
    return next(r for r in out["results"] if r["finding_id"] == fid)


def test_real_finding_with_trace_and_rule_confirmation_is_verified(repo):
    out = run([finding()], repo, EV)
    r = status(out)
    assert r["status"] == "VERIFIED", r
    assert r["checks"]["snippet_present"] and r["checks"]["rule_recheck"] and r["checks"]["trace_inspected"]
    assert out["summary"]["verified"] == 1 and out["summary"]["verification_rate"] == 1.0


def test_fabricated_evidence_is_rejected(repo):
    r = status(run([finding(evidence="subprocess.call(user_input, shell=True)")], repo, EV))
    assert r["status"] == "REJECTED" and "does not appear" in r["reason"]


def test_nonexistent_file_rejected(repo):
    r = status(run([finding(file="src/ghost.py")], repo, EV))
    assert r["status"] == "REJECTED" and r["checks"]["file_exists"] is False


def test_path_traversal_in_claim_is_rejected_not_read(repo):
    r = status(run([finding(file="../../etc/passwd")], repo, EV))
    assert r["status"] == "REJECTED"


def test_line_beyond_eof_rejected(repo):
    assert status(run([finding(line=999)], repo, EV))["status"] == "REJECTED"


def test_uncertain_not_silently_promoted_when_agent_never_inspected_file(repo):
    r = status(run([finding(confidence=0.4)], repo, [], trace=[]))
    assert r["status"] == "UNCERTAIN"
    assert any("trace" in m for m in r["missingEvidence"])


def test_wrong_line_but_near_still_matches_within_window(repo):
    assert status(run([finding(line=3)], repo, EV))["status"] == "VERIFIED"


def test_schema_violation_rejected(repo):
    bad = finding()
    del bad["remediation"]
    assert status(run([bad], repo, EV))["status"] == "REJECTED"


def test_duplicate_ids_rejected(repo):
    out = run([finding(), finding()], repo, EV)
    assert [r["status"] for r in out["results"]] == ["VERIFIED", "REJECTED"]


def test_masked_secret_evidence_matches(repo):
    f = finding(id="S2", category="secrets", line=2, evidence="AWS = 'AKIA****'", title="AWS key")
    assert status(run([f], repo, EV), "S2")["status"] in ("VERIFIED", "UNCERTAIN")
    assert status(run([f], repo, EV), "S2")["checks"]["snippet_present"] is True


def test_judge_cannot_verify_alone_but_can_downgrade(repo):
    # no trace, no external evidence, low prior: even a confident judge cannot lift it to VERIFIED
    yes = lambda c, ctx: {"supports_claim": True, "confidence": 1.0, "reason": "ok"}  # noqa: E731
    assert status(run([finding(confidence=0.3)], repo, [], trace=[], judge=yes))["status"] != "VERIFIED"
    # strongly contradicting judge downgrades an otherwise verified finding to UNCERTAIN
    no = lambda c, ctx: {"supports_claim": False, "confidence": 0.95, "reason": "benign"}  # noqa: E731
    assert status(run([finding()], repo, EV, judge=no))["status"] == "UNCERTAIN"


def test_judge_unavailable_is_recorded_not_assumed(repo):
    r = status(run([finding()], repo, EV, judge=lambda c, ctx: None))
    assert r["checks"]["judge"] == "unavailable"


def test_dependency_finding_needs_osv_evidence(repo):
    dep = finding(id="D1", category="dependency", file="package.json", line=None, package="lodash", version="4.17.15",
                  evidence="lodash 4.17.15", description="GHSA-xxxx-yyyy prototype pollution", references=["GHSA-xxxx-yyyy"])
    osv = [{"source": "osv.dev", "url": "https://osv.dev/vulnerability/GHSA-xxxx-yyyy", "query": "npm:lodash@4.17.15",
            "timestamp": "t", "snippet": "Prototype pollution", "agent": "dependency_scanner", "finding_ids": []}]
    assert status(run([dep], repo, osv, trace=[]), "D1")["status"] == "VERIFIED"
    assert status(run([dep], repo, [], trace=[]), "D1")["status"] == "UNCERTAIN"
    fake = dict(dep, id="D2", package="not-a-dep")
    assert status(run([fake], repo, osv, trace=[]), "D2")["status"] == "REJECTED"


def test_no_repository_means_uncertain(repo):
    out = verify_findings([finding()], repo_dir=None, evidence=[], tool_calls=[], policy=POL)
    assert out["results"][0]["status"] == "UNCERTAIN"


def test_evidence_window_helper():
    lines = ["a = 1", "b = 2   ", "c  =  3"]
    assert evidence_in_window("b = 2", lines, 2)
    assert evidence_in_window("c = 3", lines, 3)
    assert not evidence_in_window("zzz", lines, 2)
    assert not evidence_in_window("", lines, 2)
