# Security

## Identity, sessions, tenancy
* Passwords: Argon2id. Sessions: random 256-bit token in an `HttpOnly; SameSite=Lax` cookie (`Secure` in production); only its SHA-256 is stored.
* CSRF: per-session token required in `X-CSRF-Token` on every non-GET request.
* **Workspace isolation is server-side.** `X-Workspace-Id` is only a selector; the API honours it only if the authenticated user is a member. Every query filters by the derived workspace; foreign ids return `404` (never `403`, which would confirm existence). Covered by `tests/test_api_security.py` across ~35 endpoints.
* Rate limits (Postgres, multi-replica safe): auth, compile, execute, general API, tools.
* Security headers on API and web; HSTS in production; strict CORS (same-origin through the proxy).

## Agents are untrusted
* **Channel separation** in every prompt: SYSTEM POLICY and USER GOAL are the only authoritative channels. Repository files, web pages, tool outputs and other agents' outputs are wrapped in `<untrusted_data>` fences (fence-breakout sequences are neutralised).
* **Policy before every tool call** (`forge/policy.py`): tool granted to node, node holds every needed permission, permission allowed by workflow policy and by the agent definition, workspace match, and human approval for side-effecting tools. A denial is recorded (`tool_calls.decision = DENY`, `POLICY_BLOCKED` event, banner "No changes were made") and the node fails with `policy_violation` (never retried).
* Even a fully prompt-injected model cannot write: see `test_obeying_prompt_injection_is_blocked_by_policy_not_by_trust`.

## Filesystem sandbox
* Agents only see `{WORKSPACES_ROOT}/{workspace}/projects/{project}/runs/{run}/repo/`, built from UUIDs only.
* `safe_resolve` blocks absolute paths, drive letters, `~`, `..`, NUL bytes, `.git`, and symlinks anywhere on the path.
* Imports: GitHub URLs restricted to `https://github.com/<owner>/<repo>` (no SSRF to other hosts), clone with hooks disabled, no submodules, no symlinks, shallow, time-limited; ZIPs checked for zip-slip, absolute paths, symlink entries, entry count and expanded size (zip bombs). `.git` and symlinks are stripped from snapshots.
* Patches are path-validated before `git apply`, and applied only to the run's isolated copy. FORGE never pushes upstream. The diff shown to approvers is recomputed by FORGE, not reported by the agent.
* Tests run in a container: `--network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 1g`, no host environment.

## Secrets
* Secrets only in server environment variables; never in prompts, logs (structured logs redact key/secret/token/password/cookie fields and bearer tokens), API responses (Settings shows "configured", not values), or child-process environments (`minimal_env`).
* GitHub tokens for private repos are passed to git through environment config, never argv, never stored, and scrubbed from error messages.
* Regex scanning masks secret values in snippets (`AKIA****`).

## Data integrity
* Workflow versions immutable (DB trigger); events and audit log append-only (DB triggers); foreign keys and unique constraints everywhere; idempotency keys for run creation and replay.

## Reporting
Please report vulnerabilities privately to the maintainers (see repository security tab).
