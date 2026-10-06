# FORGE

**Don't trust an agent. Verify the workflow.**

FORGE turns natural-language goals into typed, observable and permission-controlled agent workflows powered by **Nebius Token Factory** and **NVIDIA Nemotron**. It routes models per task, enforces budgets and capabilities, validates every agent-to-agent handoff, independently verifies results against evidence, recovers from failures, and lets you replay and compare complete executions.

The first production workflow is **Repository Security Analysis & Remediation**: connect a repository, describe the objective, inspect and approve the generated workflow, watch agents scan in parallel, see which findings are verified, approve proposed patches (applied only in an isolated copy, tested in a sandbox), and get a report built from verified results only.

> Hackathon: Nebius × NVIDIA Global AI Hackathon 2026 · Track: **Best Apps and Agents** · Team: Soham Joshi, Chitrangad Sapate, Ruturaj Nalbalwar · License: Apache-2.0

---

## Why

Agent demos look great until they hallucinate a vulnerability, call a tool they shouldn't, silently burn your budget, or can't be reproduced. FORGE treats an agent workflow as a **program**, not a prompt chain:

| Prompt chain | FORGE |
|---|---|
| Free-text between agents | Typed handoffs validated against JSON Schema (`HANDOFF_FAILED` → recovery policy) |
| "The LLM says it's correct" | Verification engine checks the cited file/line, the quoted evidence, an independent rule re-check, the agent's tool trace and external advisories |
| Any tool, any time | Capability checks before **every** tool call; writes require a human approval gate |
| Unknown spend | Budget reservation before every call; execution actually **stops** |
| One-shot | Durable queue, checkpoints, retries with fallback models, worker-crash recovery, pause/resume/cancel |
| Not reproducible | Immutable workflow versions, frozen run config, replay with same/different model, run comparison |

## How NVIDIA Nemotron and Nebius Token Factory are used

* **All inference** goes through Nebius Token Factory's OpenAI-compatible API (`forge/providers/nebius.py`). Structured output (`response_format: json_schema`, with fallbacks), retries with `Retry-After`, rate-limit and timeout classification, per-provider concurrency, and real token usage recorded per call.
* **Nemotron tiered routing** (`forge/workflow/router.py`), explainable and deterministic:
  * **Nemotron 3 Ultra**: workflow compilation, independent verification judge, high-risk reasoning
  * **Nemotron 3 Super**: scanners, fix planning, balanced reasoning
  * **Nemotron 3 Nano**: planning, routine/low-risk steps
  * Each node records *why* it got its model (complexity, risk, latency, budget, availability) and a different-tier fallback.
* **Compiler** (`forge/compiler.py`): Nemotron decomposes the goal into a task spec; a deterministic assembler builds the typed DAG (parallel fan-out, joins, verification, mandatory approval gates) and validates it. Invalid graphs are never emitted.
* **Verifier independence**: the judge prefers a *different* model from the ones that produced the claims, and can only ever *downgrade* a verdict.
* Token Factory made it practical to run many specialised agents in parallel on open models, routing cheap steps to Nano and saving Ultra for the work that needs it. Cost is computed from provider-reported tokens × a configurable price table. **If no price is configured, FORGE shows "Not available"; it never guesses.**

Other Nebius services: designed for **Nebius AI Cloud** Compute (VM), **Managed Service for PostgreSQL**, **Object Storage** (S3, `https://storage.<region>.nebius.cloud`) and the container registry. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Architecture (short)

```
Browser ─► Next.js (web) ─► FastAPI (api) ─► Postgres (state, events, durable job queue)
                                   │                     ▲
                                   ▼                     │ lease / heartbeat / fence
                         Workflow compiler          Worker(s) ─► Nebius Token Factory (Nemotron)
                         (Nemotron Ultra)                │   ─► Tools (repo, pattern scan, OSV, Tavily, sandboxed tests)
                                                         │   ─► Verification engine
                                                         ▼
                                                   Object storage (snapshots, large artifacts)
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · IR: [docs/WORKFLOW_IR.md](docs/WORKFLOW_IR.md) · API: [docs/API.md](docs/API.md) · Security: [docs/SECURITY.md](docs/SECURITY.md), [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) · Ops: [docs/OPERATIONS.md](docs/OPERATIONS.md) · Reproducibility: [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md)

## Run it locally

Prerequisites: Python 3.11+, Node 20+, git, and either Docker (recommended) or the pip-installable Postgres below.

```bash
git clone <this repo> forge && cd forge
cp .env.example .env            # then set NEBIUS_API_KEY and the NEBIUS_MODEL_* ids
```

**1. Database (pick one)**

```bash
docker compose up -d postgres minio          # Postgres + S3-compatible storage
```
or, without Docker:
```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]" pgserver   # (Linux/macOS: .venv/bin/...)
.venv/Scripts/python scripts/dev_db.py        # prints a DATABASE_URL; put it in .env
```

**2. Backend**

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/alembic upgrade head            # migrations
.venv/Scripts/uvicorn forge.api.main:app --port 8000
.venv/Scripts/python -m forge.engine.worker   # in another terminal: the execution worker
```

**3. Frontend**

```bash
cd frontend && npm ci && npm run dev          # http://localhost:3000
```

Sign up, create a project, connect a GitHub repo or upload a ZIP, click **New workflow**, inspect, **Approve**, **Execute**.

**Optional seed + no-key preview.** `python scripts/seed_demo.py` creates a demo project with an intentionally vulnerable fixture repo and starts a run. Without a Nebius key you can watch the full UI with the *test-only* scripted worker `python -m tests.scripted_worker`, which uses the same scripted model the test suite uses and refuses to start in production. Every real run uses Nebius.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest          # 215 tests: engine, verification, policy, sandbox, API, e2e
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

Tests run against a real Postgres (separate `forge_test` database, real migrations). The only scripted parts are the LLM and external HTTP (OSV/Tavily). A live Nebius round-trip test runs when `NEBIUS_API_KEY_LIVE` is set.

## Production

Single Nebius VM with Docker Compose + Caddy (automatic HTTPS), Nebius Managed PostgreSQL and Object Storage. Step by step: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

| Component | Where it runs |
|---|---|
| Frontend | `web` container (Next.js standalone) behind Caddy |
| Backend API | `api` container (FastAPI/uvicorn) |
| Workers | `worker` container(s), horizontally scalable |
| Database | Nebius Managed Service for PostgreSQL |
| Queue | Postgres (`jobs` table, `SKIP LOCKED` leases): no extra broker |
| AI inference | Nebius Token Factory (NVIDIA Nemotron) |
| Object storage | Nebius Object Storage (S3 API) |
| Secrets | `.env` on the VM / Nebius secret store; never in git, never in prompts |

## Known limitations (honest)

See [docs/OPERATIONS.md#limitations](docs/OPERATIONS.md#limitations). Highlights: GitHub OAuth is not implemented (public repos + one-off tokens only); PDF export is not implemented (Markdown/JSON/ZIP are); the test sandbox needs Docker on the worker host; rate limits are fixed-window; the model router is rule-based, not learned.

## License

Apache License 2.0. See [LICENSE](LICENSE).
