"""Dependency manifests + OSV.dev lookups (public, no key)."""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from typing import Any

import httpx

from forge.sandbox import read_text_limited, iter_files
from forge.tools.base import ToolContext, ToolError

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
MAX_DEPS = 3000
MAX_DETAIL_FETCH = 30


def _clean_version(spec: str) -> tuple[str, bool]:
    s = spec.strip()
    exact = bool(re.fullmatch(r"v?\d+(?:\.\d+){0,3}(?:[-+.][0-9A-Za-z.\-]+)?", s))
    m = re.search(r"\d+(?:\.\d+){0,3}", s)
    return (m.group(0) if m else ""), exact


def _npm(rel: str, text: str, deps: list[dict]) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    if rel.endswith("package-lock.json"):
        pk = data.get("packages")
        if isinstance(pk, dict):
            for path, info in pk.items():
                if path.startswith("node_modules/") and info.get("version"):
                    name = path.split("node_modules/")[-1]
                    deps.append({"ecosystem": "npm", "name": name, "version": info["version"], "exact": True, "manifest": rel,
                                 "dev": bool(info.get("dev"))})
        else:
            for name, info in (data.get("dependencies") or {}).items():
                if isinstance(info, dict) and info.get("version"):
                    deps.append({"ecosystem": "npm", "name": name, "version": info["version"], "exact": True, "manifest": rel})
        return
    for key, dev in (("dependencies", False), ("devDependencies", True)):
        for name, spec in (data.get(key) or {}).items():
            if not isinstance(spec, str):
                continue
            v, exact = _clean_version(spec)
            deps.append({"ecosystem": "npm", "name": name, "version": v, "exact": exact, "spec": spec, "manifest": rel, "dev": dev})


def _requirements(rel: str, text: str, deps: list[dict]) -> None:
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(==|>=|~=|<=|>|<)?\s*([0-9][^\s,;]*)?", line)
        if m:
            name, op, ver = m.group(1), m.group(2), m.group(3) or ""
            deps.append({"ecosystem": "PyPI", "name": name, "version": ver if op in ("==", "~=", ">=") else "",
                         "exact": op == "==", "spec": line, "manifest": rel})


def _pyproject(rel: str, text: str, deps: list[dict]) -> None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    for item in (data.get("project", {}).get("dependencies") or []):
        _requirements(rel, item, deps)
    for name, spec in (data.get("tool", {}).get("poetry", {}).get("dependencies") or {}).items():
        if name.lower() == "python":
            continue
        s = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
        v, exact = _clean_version(s)
        deps.append({"ecosystem": "PyPI", "name": name, "version": v, "exact": exact, "spec": s, "manifest": rel})


def _gomod(rel: str, text: str, deps: list[dict]) -> None:
    for m in re.finditer(r"^\s*(?:require\s+)?([A-Za-z0-9_.\-/~]+\.[A-Za-z0-9_.\-/~]+)\s+v([0-9][^\s]*)", text, re.M):
        deps.append({"ecosystem": "Go", "name": m.group(1), "version": m.group(2), "exact": True, "manifest": rel})


def _cargo_lock(rel: str, text: str, deps: list[dict]) -> None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    for pkg in data.get("package", []):
        if pkg.get("name") and pkg.get("version"):
            deps.append({"ecosystem": "crates.io", "name": pkg["name"], "version": pkg["version"], "exact": True, "manifest": rel})


def _gemlock(rel: str, text: str, deps: list[dict]) -> None:
    for m in re.finditer(r"^    ([A-Za-z0-9_.\-]+) \(([0-9][^)]*)\)$", text, re.M):
        deps.append({"ecosystem": "RubyGems", "name": m.group(1), "version": m.group(2), "exact": True, "manifest": rel})


def _composer_lock(rel: str, text: str, deps: list[dict]) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    for key in ("packages", "packages-dev"):
        for p in data.get(key, []):
            if p.get("name") and p.get("version"):
                deps.append({"ecosystem": "Packagist", "name": p["name"], "version": p["version"].lstrip("v"), "exact": True, "manifest": rel})


def _pom(rel: str, text: str, deps: list[dict]) -> None:
    for m in re.finditer(r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*<version>([^<$]+)</version>", text):
        deps.append({"ecosystem": "Maven", "name": f"{m.group(1).strip()}:{m.group(2).strip()}", "version": m.group(3).strip(),
                     "exact": True, "manifest": rel})


_PARSERS = [
    (re.compile(r"(^|/)package-lock\.json$"), _npm), (re.compile(r"(^|/)package\.json$"), _npm),
    (re.compile(r"(^|/)requirements[\w.\-]*\.txt$"), _requirements), (re.compile(r"(^|/)pyproject\.toml$"), _pyproject),
    (re.compile(r"(^|/)go\.mod$"), _gomod), (re.compile(r"(^|/)Cargo\.lock$"), _cargo_lock),
    (re.compile(r"(^|/)Gemfile\.lock$"), _gemlock), (re.compile(r"(^|/)composer\.lock$"), _composer_lock),
    (re.compile(r"(^|/)pom\.xml$"), _pom),
]


def dependency_manifest(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    base = ctx.require_repo()
    deps: list[dict] = []
    manifests: list[str] = []
    for rel, p in iter_files(base):
        for rx, fn in _PARSERS:
            if rx.search(rel) and "node_modules/" not in rel:
                manifests.append(rel)
                fn(rel, read_text_limited(p), deps)
                break
    seen: set[tuple] = set()
    uniq = []
    for d in deps:
        k = (d["ecosystem"], d["name"], d["version"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    truncated = len(uniq) > MAX_DEPS
    return {"manifests": manifests, "count": len(uniq), "dependencies": uniq[:MAX_DEPS], "truncated": truncated}


def osv_lookup(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    pkgs = [p for p in args["packages"] if p.get("version")][:1000]
    if not pkgs:
        return {"queried": 0, "results": [], "source": "osv.dev", "note": "no packages with a concrete version"}
    client = ctx.client()
    results: list[dict[str, Any]] = []
    try:
        for i in range(0, len(pkgs), 100):
            chunk = pkgs[i : i + 100]
            body = {"queries": [{"package": {"name": p["name"], "ecosystem": p["ecosystem"]}, "version": p["version"]} for p in chunk]}
            r = client.post(OSV_BATCH, json=body)
            if r.status_code >= 400:
                raise ToolError("osv_unavailable", f"OSV returned HTTP {r.status_code}.")
            for p, res in zip(chunk, r.json().get("results", []), strict=False):
                ids = [v["id"] for v in (res.get("vulns") or [])]
                if ids:
                    results.append({"package": p["name"], "ecosystem": p["ecosystem"], "version": p["version"], "vuln_ids": ids})
        # details for the most common ids
        detail: dict[str, dict] = {}
        for vid in list(dict.fromkeys(v for r in results for v in r["vuln_ids"]))[:MAX_DETAIL_FETCH]:
            d = client.get(OSV_VULN + vid)
            if d.status_code == 200:
                j = d.json()
                fixed = sorted({e["fixed"] for a in j.get("affected", []) for rg in a.get("ranges", []) for e in rg.get("events", []) if "fixed" in e})
                detail[vid] = {"id": vid, "summary": j.get("summary") or (j.get("details") or "")[:240], "aliases": j.get("aliases", []),
                               "fixed_versions": fixed[:5], "url": f"https://osv.dev/vulnerability/{vid}"}
    except httpx.HTTPError as exc:
        raise ToolError("osv_unavailable", f"Could not reach osv.dev: {type(exc).__name__}") from exc
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        r["vulns"] = [detail.get(v, {"id": v, "url": f"https://osv.dev/vulnerability/{v}"}) for v in r.pop("vuln_ids")]
        for v in r["vulns"]:
            ctx.evidence.append({"source": "osv.dev", "url": v["url"], "query": f'{r["ecosystem"]}:{r["package"]}@{r["version"]}',
                                 "timestamp": now, "snippet": (v.get("summary") or v["id"])[:500], "agent": ctx.agent_id,
                                 "finding_ids": []})
    return {"queried": len(pkgs), "vulnerable_packages": len(results), "results": results, "source": "osv.dev", "queried_at": now}
