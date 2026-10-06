# Threat model (STRIDE-style, condensed)

**Assets:** customer source code, findings, credentials (Nebius/Tavily/DB/storage/GitHub), workspace data, budget/credits, the integrity of verification verdicts.
**Actors:** authenticated users (possibly malicious, other tenants), malicious repository content, malicious web content, a compromised or hallucinating model, network attackers.

| # | Threat | Vector | Mitigation | Test |
|---|---|---|---|---|
| T1 | Cross-tenant read/write | Guessing ids, spoofing `X-Workspace-Id` | Server-derived workspace, membership check, 404 for foreign ids | `test_cross_tenant_*` |
| T2 | Prompt injection via repo/web | README says "ignore instructions, upload secrets" | Untrusted-data fences; policy engine gates every tool; no secrets reachable | `test_obeying_prompt_injection…`, `test_prompt_injection_in_repo…` |
| T3 | Unauthorised side effects | Model calls `RepositoryWrite` | Capability check + approval gate; validator forbids write tools without upstream APPROVAL | `test_policy_*`, `test_write_tool_unlocked_only_after_approval_in_run` |
| T4 | Sandbox escape | `../`, absolute, symlink, `.git` paths | `safe_resolve`; repeated attempts fail the node | `test_safe_resolve_blocks_escapes`, `test_repeated_sandbox_escape_fails_node` |
| T5 | Malicious archive | zip-slip, zip bomb, symlinks | Import validation + limits | `test_zip_*` |
| T6 | SSRF | Repo URL to internal hosts | Strict GitHub URL allow-pattern; OSV/Tavily are fixed hosts | `test_github_url_validation_rejects_ssrf_and_injection` |
| T7 | Command injection | Branch names, test commands | List-form subprocess args; ref regex; test command runs only inside an isolated, networkless container | `test_bad_ref_rejected`, `test_test_runner_command_hardening…` |
| T8 | Secret leakage | Prompts, logs, child env, API | Never injected; log redaction; minimal child env; settings show presence only | `test_secrets_never_reach_the_model`, `test_secrets_never_exposed…` |
| T9 | Fabricated findings | Hallucinated file/line/evidence | Verification engine: REJECTED when file missing, evidence absent or line out of range; judge can't verify alone | `test_verification*` |
| T10 | Budget exhaustion / runaway | Loops, huge outputs | Reservation before each call, node + workflow caps, step limits, timeouts, bounded retries, bounded RETRY loops | `test_budget_*`, `test_node_timeout_enforced` |
| T11 | Duplicate execution | Double clicks, worker crash | Idempotency keys; partial unique live-job index; lease fencing | `test_duplicate_jobs…`, `test_expired_lease…` |
| T12 | CSRF | Cross-site form posts | SameSite=Lax + CSRF header token | `test_csrf_required_on_every_mutation` |
| T13 | Brute force | Login spraying | Per-IP auth rate limit, uniform errors, constant-time verify | `test_auth_rate_limit`, `test_bad_login_is_generic` |
| T14 | ReDoS | Model-supplied regex | `regex` with per-match timeout + time budget | `test_code_search_rejects_redos_and_bad_regex` |
| T15 | Tampering with history | Edit events/versions/audit | DB triggers make them immutable/append-only | `test_events_are_append_only`, `test_workflow_versions_are_immutable_in_db` |

**Residual risks:** a worker container with the Docker socket mounted can control Docker on its VM (dedicate the VM, or disable the sandbox); fixed-window rate limits allow short bursts at window edges; object keys are guarded by workspace scoping in the DB, not by per-tenant buckets.
