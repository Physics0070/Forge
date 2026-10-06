"""Verification engine.

It does NOT ask an LLM "is this correct?". For every claimed finding it inspects:
  claim        schema validity, internal consistency
  source       the cited file/line in the run's repository snapshot
  artifact     whether the quoted evidence really appears at that location
  tool output  independent re-run of deterministic rules at the cited location
  trace        whether the producing agent actually looked at that file (tool-call log)
  evidence     external evidence collected in this run (OSV / Tavily) supporting the claim
An independent model (different from the producer) is ONE bounded signal; it can lower a verdict
to UNCERTAIN but can never produce VERIFIED on its own.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

from forge import schemas as S
from forge.sandbox import SandboxError, is_probably_text, read_text_limited, safe_resolve
from forge.tools.base import ToolContext
from forge.tools.deps import dependency_manifest
from forge.tools.patterns import RULES, scan_file
from forge.workflow.ir import VerificationPolicy

# judge(claim, code_context) -> {"supports_claim": bool, "confidence": float, "reason": str} | None (unavailable)
Judge = Callable[[dict[str, Any], str], dict[str, Any] | None]

W_SNIPPET, W_RULE, W_TRACE, W_EXTERNAL, W_JUDGE = 0.35, 0.20, 0.15, 0.20, 0.10
_MASK_SPLIT = re.compile(r"\*{3,}|\[REDACTED\]")
DEP_CATEGORIES = {"dependency", "vulnerable_dependency", "dependencies", "supply_chain"}


@dataclass
class VerificationResult:
    finding_id: str
    status: str  # VERIFIED | REJECTED | UNCERTAIN
    confidence: float
    evidence_score: float
    reason: str
    missing_evidence: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"finding_id": self.finding_id, "status": self.status, "confidence": round(self.confidence, 3),
                "evidenceScore": round(self.evidence_score, 3), "reason": self.reason,
                "missingEvidence": self.missing_evidence, "recommendations": self.recommendations, "checks": self.checks}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def evidence_in_window(evidence: str, lines: list[str], line: int | None, radius: int = 3) -> bool:
    """Is the quoted evidence (masking-aware) present near `line` (or anywhere if line is unknown)?"""
    if not evidence.strip():
        return False
    if line is None or line < 1:
        window = " ".join(lines)
    else:
        lo, hi = max(0, line - 1 - radius), min(len(lines), line + radius)
        window = " ".join(lines[lo:hi])
    w = _norm(window)
    chunks = [_norm(c) for c in _MASK_SPLIT.split(evidence) if len(_norm(c)) >= 4]
    if not chunks:
        return False
    # window lines are joined and whitespace-normalised, so multi-line evidence still matches
    return all(c in w for c in chunks)


def _rule_recheck(repo_dir: Path, rel: str, p: Path, line: int | None, category: str) -> tuple[bool | None, bool]:
    """(supports?, in_test_path). None = no deterministic rule covers this category."""
    cat_rules = [r for r in RULES if r.category == category]
    rules = cat_rules or RULES
    hits = scan_file(rules, rel, p)
    if not cat_rules and not hits:
        return None, False
    near = [h for h in hits if line is None or abs(h["line"] - line) <= 3]
    return (bool(near), any(h["in_test_path"] for h in near))


def _trace_saw_file(rel: str, tool_calls: list[dict[str, Any]]) -> bool:
    for tc in tool_calls:
        inp = tc.get("input") or {}
        out = tc.get("output_summary") or {}
        if str(inp.get("path", "")).replace("\\", "/") == rel:
            return True
        if rel in (out.get("files") or []):
            return True
    return False


def verify_findings(
    findings: list[dict[str, Any]],
    *,
    repo_dir: Path | None,
    evidence: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    policy: VerificationPolicy,
    judge: Judge | None = None,
    ctx: ToolContext | None = None,
) -> dict[str, Any]:
    results: list[VerificationResult] = []
    deps_index: dict[tuple[str, str], str] | None = None
    seen_ids: set[str] = set()
    for f in findings:
        fid = str(f.get("id", "")) or f"unnamed-{len(results)}"
        missing: list[str] = []
        recs: list[str] = []
        checks: dict[str, Any] = {}

        # 1) schema + uniqueness
        try:
            jsonschema.validate(f, S.SECURITY_FINDING)
            checks["schema"] = True
        except jsonschema.ValidationError as exc:
            results.append(VerificationResult(fid, "REJECTED", 0.0, 0.0, f"Finding violates the SecurityFinding schema: {exc.message[:200]}",
                                              ["valid SecurityFinding"], ["Re-emit the finding with all required fields."], {"schema": False}))
            continue
        if fid in seen_ids:
            results.append(VerificationResult(fid, "REJECTED", 0.0, 0.0, "Duplicate finding id.", [], ["Use unique ids."], {"unique_id": False}))
            continue
        seen_ids.add(fid)

        category = str(f.get("category", "")).lower()
        is_dep = category in DEP_CATEGORIES or bool(f.get("package"))
        if repo_dir is None:
            results.append(VerificationResult(fid, "UNCERTAIN", 0.0, 0.0, "No repository snapshot available to verify against.",
                                              ["repository snapshot"], [], {"repository": False}))
            continue

        # 2) the cited source exists
        try:
            p = safe_resolve(repo_dir, str(f["file"]), must_exist=True)
            if not p.is_file() or not is_probably_text(p):
                raise SandboxError("not a text file")
            rel = p.resolve().relative_to(repo_dir.resolve()).as_posix()
            lines = read_text_limited(p).splitlines()
            checks["file_exists"] = True
        except SandboxError:
            results.append(VerificationResult(
                fid, "REJECTED", 0.0, 0.0, f"Cited file '{f.get('file')}' does not exist in the repository snapshot.",
                ["existing source file"], ["Drop or correct the file path."], {"file_exists": False}))
            continue

        earned = 0.0
        applicable = 0.0
        line = f.get("line")
        hard_fail: str | None = None

        if is_dep:
            if deps_index is None:
                deps_index = {}
                dctx = ctx or ToolContext(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "verify", "verifier", repo_dir, None)  # type: ignore[arg-type]
                dctx.repo_dir = repo_dir
                for d in dependency_manifest(dctx, {})["dependencies"]:
                    deps_index[(d["name"].lower(), d.get("version", ""))] = d["manifest"]
                    deps_index.setdefault((d["name"].lower(), ""), d["manifest"])
            pkg, ver = str(f.get("package") or "").lower(), str(f.get("version") or "")
            applicable += W_SNIPPET
            if pkg and ((pkg, ver) in deps_index or (pkg, "") in deps_index and not ver):
                earned += W_SNIPPET
                checks["dependency_declared"] = True
            else:
                checks["dependency_declared"] = False
                hard_fail = f"Package '{f.get('package')}' version '{ver}' is not declared in any manifest."
            ids = {r.upper() for r in re.findall(r"(?i)\b(?:GHSA-[0-9a-z\-]+|CVE-\d{4}-\d+|PYSEC-\d+-\d+|OSV-[\w\-]+|GO-\d{4}-\d+|RUSTSEC-\d+-\d+)\b",
                                                 " ".join([f.get("description", ""), f.get("evidence", "")] + list(f.get("references", []))))}
            applicable += W_EXTERNAL + W_RULE
            osv_text = " ".join(e["snippet"] + " " + e["url"] for e in evidence if e.get("source") == "osv.dev").upper()
            pkg_in_osv = any(pkg and pkg in e.get("query", "").lower() for e in evidence if e.get("source") == "osv.dev")
            if pkg_in_osv and (not ids or any(i in osv_text for i in ids)):
                earned += W_EXTERNAL + W_RULE  # authoritative advisory source confirms it
                checks["osv_confirms"] = True
            else:
                checks["osv_confirms"] = False
                missing.append("OSV advisory evidence for this package/version in this run")
        else:
            # 3) quoted evidence really appears at that location
            applicable += W_SNIPPET
            ev = str(f.get("evidence", ""))
            if line and line > len(lines):
                hard_fail = f"Cited line {line} is beyond the end of '{rel}' ({len(lines)} lines)."
                checks["line_valid"] = False
            elif evidence_in_window(ev, lines, line):
                earned += W_SNIPPET
                checks["snippet_present"] = True
            else:
                checks["snippet_present"] = False
                hard_fail = hard_fail or f"Quoted evidence does not appear at {rel}:{line or '?'} (possible fabrication or wrong location)."
            if not line:
                missing.append("line number")

            # 4) independent deterministic re-check
            supports, in_test = _rule_recheck(repo_dir, rel, p, line, category)
            if supports is not None:
                applicable += W_RULE
                checks["rule_recheck"] = supports
                if supports:
                    earned += W_RULE
                else:
                    missing.append("independent rule-based confirmation near the cited line")
            if in_test:
                earned = max(0.0, earned - 0.15)
                checks["in_test_path"] = True
                recs.append("Location looks like test/example code; confirm it is reachable in production.")

            # 5) external evidence (weak for code findings)
            applicable += W_EXTERNAL
            refs = " ".join(f.get("references", [])).lower()
            ext_match = any(e.get("url") and (e["url"].lower() in refs or any(fid == x for x in e.get("finding_ids", []))) for e in evidence)
            if ext_match:
                earned += W_EXTERNAL
                checks["external_evidence"] = True
            else:
                checks["external_evidence"] = False
                missing.append("external reference supporting this issue class")

        # 6) execution trace: did the producer actually inspect it?
        applicable += W_TRACE
        dep_trace = any(tc.get("tool") in ("DependencyManifest", "OsvLookup") for tc in tool_calls)
        if (dep_trace if is_dep else _trace_saw_file(rel, tool_calls)):
            earned += W_TRACE
            checks["trace_inspected"] = True
        else:
            checks["trace_inspected"] = False
            missing.append("tool-call trace showing the agent inspected this file")

        # 7) independent judge: bounded signal
        judge_against = False
        if judge is not None and not is_dep:
            ctx_lo, ctx_hi = max(0, (line or 1) - 8), min(len(lines), (line or 1) + 8)
            code_ctx = "\n".join(f"{i + 1:>5}| {lines[i]}" for i in range(ctx_lo, ctx_hi))
            verdict = judge(f, code_ctx)
            applicable += W_JUDGE
            if verdict is None:
                checks["judge"] = "unavailable"
                missing.append("independent model opinion (judge unavailable)")
            else:
                checks["judge"] = {"supports": verdict["supports_claim"], "confidence": verdict["confidence"]}
                if verdict["supports_claim"]:
                    earned += W_JUDGE * float(verdict["confidence"])
                elif verdict["confidence"] >= 0.7:
                    judge_against = True

        score = earned / applicable if applicable else 0.0
        prior = float(f.get("confidence", 0.5))
        confidence = round(0.3 * prior + 0.7 * score, 3)

        if hard_fail:
            status, reason = "REJECTED", hard_fail
            recs.append("Do not report this finding unless the evidence is corrected.")
        elif is_dep and not checks.get("osv_confirms"):
            status, reason = "UNCERTAIN", "Dependency claim has no confirming advisory from OSV in this run."
            recs.append("Run OsvLookup for this package/version or verify manually.")
        elif judge_against:
            status, reason = "UNCERTAIN", "Deterministic checks passed but the independent reviewer disagreed."
            recs.append("Manual review required.")
        elif score >= policy.min_evidence_score and confidence >= policy.min_confidence:
            status, reason = "VERIFIED", f"Evidence confirmed independently (evidence score {score:.2f})."
        else:
            status = "UNCERTAIN"
            reason = f"Insufficient independent evidence (evidence score {score:.2f}, confidence {confidence:.2f})."
            recs.append("Treat as UNVERIFIED until confirmed manually.")
        results.append(VerificationResult(fid, status, confidence, score, reason, missing, recs, checks))

    by_id = {str(f.get("id")): f for f in findings}
    verified = [by_id[r.finding_id] for r in results if r.status == "VERIFIED" and r.finding_id in by_id]
    rejected = [by_id[r.finding_id] for r in results if r.status == "REJECTED" and r.finding_id in by_id]
    uncertain = [by_id[r.finding_id] for r in results if r.status == "UNCERTAIN" and r.finding_id in by_id]
    summary = {"total": len(results), "verified": len(verified), "rejected": len(rejected), "uncertain": len(uncertain),
               "verification_rate": round(len(verified) / len(results), 3) if results else None}
    return {"results": [r.to_dict() for r in results], "verified_findings": verified, "rejected_findings": rejected,
            "uncertain_findings": uncertain, "summary": summary}
