# Reproducibility

What FORGE records so a run can be understood and repeated:

| Recorded | Where |
|---|---|
| Exact workflow (all nodes, edges, schemas, policies, budgets) | `workflow_versions.ir` (immutable, `ir_hash`) **and** a frozen copy in `runs.ir_snapshot` |
| Run input, repository snapshot, budget caps | `runs.input`, `runs.repository_id` (content-addressed tar.gz in object storage, `commit_sha` for GitHub) |
| Model per node + why | `run_nodes.model`, `run_nodes.routing` (tier, reasons, fallback) |
| Every model call | `model_calls`: model, tokens, latency, cost + basis, request hash, provider request id |
| Every tool call | `tool_calls`: input, decision (ALLOW/DENY) + reason, result summary, duration |
| Every state change | `events` (append-only) and `checkpoints` (input/output/state per transition, agent steps) |
| Every output | `artifacts` with SHA-256 content hash and provenance (producer, model, attempt, inputs, workflow version) |
| Patches applied | `applied_patch` artifacts, in order; the run workspace is rebuilt from snapshot + patches on any worker |

## Replay
`POST /api/runs/{id}/replay` (UI: *Replay*) creates a **new** run; the original is never modified.
* `same`: identical version, input, policies and routing.
* `different_model`: same everything, all agents forced to one model (e.g. compare Super vs Ultra).
* `new_version`: same input against another approved version.
* `reuse_nodes`: copy outputs of successful nodes whose definition is byte-identical in the target version.

LLM outputs are not bit-for-bit deterministic, so a replay reproduces the **configuration** exactly, and **Compare** (`/compare`) shows where outcomes diverged: per-metric deltas and step-by-step states/models.

## Exports
* Workflow: `GET /api/workflows/{id}/versions/{n}/export` (format `forge.workflow/1`)
* Run: `GET /api/runs/{id}/export?format=json` (`forge.run/1`: run, IR, events, model/tool calls, artifacts, reproducibility block), `bundle` (ZIP incl. artifacts), `trace`, `markdown` (report)
