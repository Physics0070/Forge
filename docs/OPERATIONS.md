# Operations

## Processes
| Process | Command | Scale |
|---|---|---|
| API | `uvicorn forge.api.main:app` | stateless, N replicas |
| Worker | `python -m forge.engine.worker` | N processes × `FORGE_WORKER_CONCURRENCY` threads |
| Migrations | `alembic upgrade head` | once per deploy |

## Observability
* **System health** page / `GET /api/system/health`: worker heartbeats, queue depth and oldest queued job, provider calls/error rate/p50/p95 and errors by class (last hour), failed nodes (24 h), average run duration, sandbox/Tavily/storage status, optional live provider check.
* Structured JSON logs (`service`, `run_id`, `node_id`, `event`), with secrets redacted.
* Run-level truth: events, model calls, tool calls, checkpoints (UI: run page, node drawer, trace).

## Runbook
| Symptom | Check | Action |
|---|---|---|
| Runs stay READY | System health → workers | Start/restart workers; check DB connectivity |
| Many `rate_limited` errors | Provider errors by class | Lower `NEBIUS_MAX_CONCURRENCY` or workflow `maxParallelism` |
| `auth`/`permanent` model errors | Live check | Fix `NEBIUS_API_KEY` / model ids |
| Run BLOCKED | Run page budget panel | Increase budget or switch blocked steps to a cheaper model |
| Worker crashed mid-run | Events `WORKER_LOST` | Automatic: lease expiry → requeue → resume from checkpoint (≤ 6 recoveries) |
| Tests `NOT_AVAILABLE` | System health → sandbox | Docker socket + `DOCKER_GID` on the worker, or accept |

## Retention
Per-workspace (Settings → Data retention): events, traces (tool calls + checkpoints), artifacts (+ stored objects), memory. The worker sweeps hourly; run sandboxes older than a day are removed. Only data of finished runs is swept.

## Backups
Use Managed PostgreSQL automated backups; enable versioning on the Object Storage bucket.

## Limitations
Stated plainly. These are real, not hidden:
* **GitHub OAuth** is not implemented. Public repos work; private repos via a one-off token (never stored). No PR creation. Approved patches are exported (diff/ZIP), never pushed.
* **PDF export** is not implemented (Markdown, JSON, ZIP bundle are).
* **Model router** is deterministic rules with configurable thresholds, not learned/optimised.
* **Pricing** is a manual table; without it cost is "Not available" (token budgets still enforced).
* **Rate limiting** is fixed-window; concurrency limits are soft under heavy multi-worker contention.
* **Sandbox** requires Docker on the worker host; dependency installation inside the networkless sandbox is not attempted, so projects whose tests need packages not in the sandbox image report FAILED or NOT_AVAILABLE.
* **Dependency scanning** covers npm, PyPI, Go, crates.io, RubyGems, Packagist and simple Maven POMs; version ranges without lockfiles are checked at their lower bound only.
* **Local object storage** is single-host; multi-host deployments must use S3/Object Storage.
* Email verification and password reset are not implemented.
