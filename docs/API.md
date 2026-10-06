# API

Interactive OpenAPI docs: `GET /api/docs` (schema at `/api/openapi.json`). All endpoints are JSON, cookie-authenticated, workspace-scoped. Mutations need `X-CSRF-Token` (returned by `/api/auth/me`). Errors: `{"error": {"code", "message", …}}`.

## Auth & tenancy
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/signup` | `{email, password(≥10), workspace_name}` → session cookie + csrf |
| POST | `/api/auth/login` · `/api/auth/logout` | |
| GET | `/api/auth/me` | user, workspaces, csrf token |
| GET/POST | `/api/workspaces` | |

## Projects & repositories
| GET/POST | `/api/projects` · GET/DELETE `/api/projects/{id}` |
|---|---|
| GET | `/api/projects/{id}/repositories` |
| POST | `/api/projects/{id}/repositories/github` `{url, ref?, token?}` → 202, imports in background |
| POST | `/api/projects/{id}/repositories/zip` (multipart `file`) |
| GET/DELETE | `/api/repositories/{id}` |

## Workflows
| Method | Path | Notes |
|---|---|---|
| POST | `/api/workflows/compile` | `{project_id, goal, repository_id?, constraints}` → `{workflow, issues, meta}` or 422 `COMPILATION_FAILED {reason, suggestion, issues}` |
| GET | `/api/workflow-templates` | |
| POST | `/api/workflows` | `{project_id, ir}` or `{project_id, template, goal, include_remediation}` → v1 (draft) |
| GET | `/api/workflows?project_id=` · `/api/workflows/{id}` | latest version incl. IR + validation issues |
| GET | `/api/workflows/{id}/versions/{n}` | |
| POST | `/api/workflows/{id}/versions` | `{ir, note}` → next immutable version |
| POST | `/api/workflows/{id}/validate` | `{ir}` → issues |
| POST | `/api/workflows/{id}/versions/{n}/approve` | 422 with issues if invalid |
| POST | `/api/workflows/{id}/duplicate` · DELETE `/api/workflows/{id}` | |
| GET | `/api/workflows/{id}/versions/{n}/export` | |

## Runs
| Method | Path | Notes |
|---|---|---|
| POST | `/api/runs` | `{workflow_version_id, input:{objective}, repository_id?, budget_usd?, budget_tokens?}` + `Idempotency-Key` header → 201 (or 200 `idempotentReplay`) |
| GET | `/api/runs?project_id=&status=` · `/api/runs/{id}` | nodes, graph, totals (tokens, cost + basis, latency), budget, pending approvals |
| GET | `/api/runs/{id}/nodes/{node}` | input/output, model calls, tool calls, artifacts, verification, checkpoints, events |
| POST | `/api/runs/{id}/pause` · `/resume` · `/cancel` · `/retry?node_id=` | real worker effects |
| POST | `/api/runs/{id}/unblock` | `{action: increase_budget|retry_with_cheaper_model, max_usd?, max_tokens?, model?}` |
| POST | `/api/runs/{id}/replay` | `{mode: same|different_model|new_version, model?, workflow_version_id?, reuse_nodes[], budget_usd?}` |
| GET | `/api/runs/{id}/events?after=&type=` | paginated event log |
| GET | `/api/runs/{id}/stream` | **Server-Sent Events** (`event: forge`, `event: end`, supports `Last-Event-ID`) |
| GET | `/api/runs/{id}/artifacts?type=` · `/verification` · `/model-calls` | |
| GET | `/api/runs/{id}/export?format=json|markdown|trace|bundle` | |
| GET | `/api/runs/compare?a=&b=` | metrics + diffs |
| GET | `/api/approvals?status=` · POST `/api/approvals/{id}` `{decision: grant|reject, note}` | |

## Other
`GET /api/artifacts`, `GET /api/artifacts/{id}`, `GET /api/artifacts/{id}/download`, `GET/POST /api/agents`, `DELETE /api/agents/{key}`, `GET /api/dashboard`, `GET /api/system/health?live=`, `GET /api/settings`, `PUT /api/settings/retention`, `GET /api/audit-log`, `GET/POST /api/evaluations`, `GET /api/evaluations/{id}`, `GET /api/health`.
