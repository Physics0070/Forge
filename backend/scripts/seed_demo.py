"""Seed a LOCAL development workspace through the public API (never use against production).

    python scripts/seed_demo.py [--api http://localhost:8000]

Creates (idempotently) a demo user, a project, uploads a small intentionally-vulnerable fixture repository,
instantiates the security-audit template, approves v1 and starts one run. Pair with `python -m tests.scripted_worker`
to watch a full run without a Nebius key, or with the real worker once NEBIUS_API_KEY is configured.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.audit_script import zip_bytes  # noqa: E402

DEMO_EMAIL = "demo@forge-demo.dev"       # local test credentials only
DEMO_PASSWORD = "<set FORGE_DEMO_PASSWORD>"  # local test credentials only


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=60)
    r = c.post("/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    if r.status_code != 200:
        r = c.post("/api/auth/signup", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "workspace_name": "Demo Workspace"})
        r.raise_for_status()
    h = {"x-csrf-token": r.json()["csrf_token"]}
    projects = c.get("/api/projects").json()["projects"]
    proj = next((p for p in projects if p["name"] == "Security Audit"), None) or c.post(
        "/api/projects", json={"name": "Security Audit", "description": "Demo: audit a small intentionally vulnerable service."}, headers=h).json()
    repos = c.get(f"/api/projects/{proj['id']}/repositories").json()["repositories"]
    repo = next((x for x in repos if x["status"] == "ready"), None) or c.post(
        f"/api/projects/{proj['id']}/repositories/zip", files={"file": ("vulnerable-service.zip", zip_bytes(), "application/zip")}, headers=h).json()
    wfs = c.get(f"/api/workflows?project_id={proj['id']}").json()["workflows"]
    wf = next((w for w in wfs if w["templateKey"] == "repository-security-audit"), None) or c.post("/api/workflows", json={
        "project_id": proj["id"], "template": "repository-security-audit", "name": "Repository Security Audit",
        "goal": "Audit this repository for security vulnerabilities, verify each finding against the code, and propose fixes for the real ones."}, headers=h).json()
    c.post(f"/api/workflows/{wf['id']}/versions/1/approve", headers=h)
    wf = c.get(f"/api/workflows/{wf['id']}").json()
    run = c.post("/api/runs", json={"workflow_version_id": wf["versions"][-1]["id"], "repository_id": repo["id"],
                                    "input": {"objective": "Audit the vulnerable-service repository"}, "budget_tokens": 200000}, headers=h).json()
    print(f"user={DEMO_EMAIL}\nproject={proj['id']}\nworkflow={wf['id']}\nrun={run.get('id')}  status={run.get('status')}")


if __name__ == "__main__":
    main()
