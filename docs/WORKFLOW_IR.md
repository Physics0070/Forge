# Workflow IR (v1.0)

The IR is the contract between the compiler, the editor, the validator and the engine. It is strict (unknown fields are rejected), serialisable JSON, and content-hashed (`Workflow.content_hash()` ignores ids/timestamps). Source: `backend/forge/workflow/ir.py`.

```jsonc
{
  "irVersion": "1.0", "name": "Repository Security Audit", "goal": "…",
  "nodes": [ /* WorkflowNode */ ], "edges": [ /* WorkflowEdge */ ],
  "policies": { "allowedPermissions": ["repository.read", "…"], "requireApprovalForWrites": true, "maxParallelism": 4 },
  "memoryPolicy": { "projectMemory": "off|read|read_write", "scratchTtlHours": 24 },
  "budgetPolicy": { "maxUsd": 1.0, "maxTokens": 200000, "unpricedBehavior": "enforce_tokens_only|block" },
  "verificationPolicy": { "required": false, "minConfidence": 0.6, "minEvidenceScore": 0.5, "onFail": "fail|recover|continue_flagged" },
  "outputs": ["report"]
}
```

## WorkflowNode

| Field | Meaning |
|---|---|
| `id` | `[A-Za-z][A-Za-z0-9_-]{0,63}` |
| `type` | `AGENT · PARALLEL · JOIN · CONDITION · RETRY · APPROVAL · VERIFICATION · RECOVERY · TRANSFORM` |
| `agentId` | Built-in or workspace agent (AGENT / agent-backed RECOVERY) |
| `inputSchema` / `outputSchema` | JSON Schema 2020-12; input validated at handoff, output validated before SUCCESS |
| `model` | Explicit model id; `null` → router decides from `routing` |
| `routing` | `complexity`, `risk`, `latency`, `verificationCritical` |
| `tools`, `permissions` | Tools the node may call and the capabilities granted (≤ agent ≤ workflow policy) |
| `retryPolicy` | `maxAttempts, backoff, baseDelayS, maxDelayS, jitter, retryableErrors, validationRetries, fallbackModel` |
| `timeoutS`, `budget` | Hard per-node limits (`budget.maxUsd`, `budget.maxTokens`) |
| `verificationPolicy` | Inline verification of an agent's findings |
| `locked` | Editor cannot modify/delete |

## Node semantics (all implemented in `engine/executors.py` + `engine/readiness.py`)

| Type | Runtime behaviour |
|---|---|
| AGENT | Tool-calling loop over structured Nemotron output; final output schema-validated; checkpoint per step |
| PARALLEL | Passes input through; all outgoing branches become ready together |
| JOIN | Waits only for `config.required` (default all upstream); `mode: all|any`; `concat` merges arrays from branches |
| CONDITION | Evaluates `config.predicate` (`{path, op, value}` / `all` / `any` / `not`); only edges labelled with the result run; others SKIPPED |
| RETRY | Checks `config.until` on its direct upstream `config.target`; if false, re-runs the target with feedback (≤ `max_iterations`) |
| APPROVAL | Run pauses in WAITING_APPROVAL; grant → passes input through; reject → FAILED, or SKIPPED with `on_reject: skip` |
| VERIFICATION | Runs the verification engine over `config.findings_path` (default `$.findings`); persists per-claim results |
| RECOVERY | Armed by `config.watches`; runs only if a watched node FAILED; its output (static `fallback_output(s)` or an agent's) replaces the failed node's output (FAILED → RECOVERING → SUCCESS) |
| TRANSFORM | Deterministic reshaping: `select` / `set` with `{from}`, `{concat}`, `{count}`, literals; no model call |

## WorkflowEdge (typed handoff)

```json
{ "id": "gather__risk", "source": "gather", "target": "risk_analyzer", "kind": "data|control|failure",
  "condition": "true|false|null", "mapping": [{ "from": "$.findings", "to": "findings" }],
  "onHandoffFailure": "retry_source|fail|recover" }
```

* `mapping.from`: `$.path` into the source output, `$workflow.path` into the run input, or `$const` with `const`.
* No mapping: the single source's output becomes the input (multiple unmapped sources are keyed by source id).
* The assembled input is validated against the target's `inputSchema`. On failure: `HANDOFF_FAILED` event, then the edge policy (`retry_source` re-runs the source with the validation errors as feedback, at most 2 times).
* `failure` edges activate only when the source FAILED.

## Versioning

Every save creates a new immutable `workflow_versions` row (a DB trigger rejects updates to `ir`). A run references `workflow_version_id` **and** stores a frozen `ir_snapshot`. Only approved versions without validation errors can run.
