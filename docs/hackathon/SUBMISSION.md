# Devpost submission kit: FORGE

Track: **Best Apps and Agents** (agent workflow runtime for a real job: auditing and fixing code security issues; Nemotron Ultra for hard reasoning, Nano/Super for fast steps, as the track describes).

---

## Placeholders only the team can fill

| # | Item | Status |
|---|---|---|
| 1 | Nebius Token Factory API key + the exact Nemotron Nano/Super/Ultra model ids (`GET /v1/models`) → `.env` | **TODO** |
| 2 | Model prices from the Token Factory console → `backend/forge/providers/pricing.json` (otherwise cost shows "Not available") | **TODO** |
| 3 | Tavily API key (optional; Builders Program credits) → `TAVILY_API_KEY` | **TODO** |
| 4 | Nebius AI Cloud: VM + Managed PostgreSQL + Object Storage bucket/keys + DNS name (docs/DEPLOYMENT.md) | **TODO** |
| 5 | Public GitHub repo URL (MIT license is in place; make sure "MIT" shows in the About box) | **TODO** |
| 6 | Hosted demo URL + a judge test account (put credentials in the Devpost "testing instructions", not in the repo) | **TODO** |
| 7 | YouTube video (public, < 3:00, no copyrighted music) | **TODO** |
| 8 | Representative who submits on Devpost; Builders & Brews city (if attended) | **TODO** |
| 9 | Run one real end-to-end audit on Nebius and keep the run link for the video | **TODO** |

---

## Project description (paste into Devpost)

**FORGE: Don't trust an agent. Verify the workflow.**

AI agents are great at producing *plausible* output. For real engineering work, such as auditing a codebase for security issues, plausible isn't enough. Agents invent file paths, misread code, call tools they shouldn't, burn budget in loops, and leave you with nothing you can reproduce.

FORGE is an agent workflow runtime that treats an agent workflow like a program instead of a prompt chain. You give it a goal and a repository. **NVIDIA Nemotron on Nebius Token Factory** compiles the goal into a **typed, validated DAG** of specialised agents. You inspect and edit every node: model, tools, permissions, budget, timeout, retries. Then you approve an immutable version and run it.

During execution FORGE:
- **routes each step to the right Nemotron tier** (Nano for routine steps, Super for scanning, Ultra for compilation, risk and verification) and shows why;
- runs independent branches **in parallel** on a durable Postgres-backed queue, with checkpoints, retries with fallback models, and recovery from worker crashes;
- **validates every handoff** between agents against JSON Schema;
- checks **permissions before every tool call**: a prompt-injected agent that tries to write code is blocked, and the UI shows exactly what was attempted and that nothing changed;
- **enforces budgets** by reserving spend before each call, so a run stops *before* overspending;
- **independently verifies every finding**: is the file real, does the quoted code exist at that line, does a deterministic rule agree, did the agent actually read the file, does an OSV advisory or a Tavily source back it up. A second Nemotron model can only lower a verdict, never create one. Fabricated findings are rejected; uncertain ones are never shown as verified;
- puts code changes behind **human approval gates**, applies them only to an isolated copy, runs tests in a networkless sandbox, and shows a diff that FORGE computed itself;
- lets you **replay** any run with the same configuration, a different model or a new workflow version, and **compare** runs metric by metric.

Everything in the UI comes from real execution data. When a number doesn't exist (for example cost when no price is configured), FORGE says "Not available" instead of inventing one.

**Built with:** NVIDIA Nemotron 3 (Nano / Super / Ultra) via Nebius Token Factory (OpenAI-compatible API, structured output), Nebius AI Cloud (Compute VM, Managed PostgreSQL, Object Storage), FastAPI, PostgreSQL, Next.js, React Flow, Tavily, OSV.dev.

### How we used NVIDIA Nemotron and Nebius Token Factory
- One provider abstraction (`ModelProvider`) with a real `NebiusProvider` against Token Factory's OpenAI-compatible endpoint: JSON-schema structured output with graceful fallbacks, `Retry-After`-aware retries, error classification (rate limit / timeout / auth / invalid output), per-provider concurrency limits, and provider-reported token usage recorded for every call.
- An explainable tier router maps task complexity, risk, latency and verification criticality to Nemotron Nano, Super or Ultra, with a different-tier fallback.
- Nemotron Ultra compiles goals into task specs, and FORGE's deterministic assembler turns them into typed DAGs (parallel fan-out, joins, mandatory approval gates, verification).
- Nemotron acts as an *independent* verifier: it prefers a different model than the one that produced the claim.
- Token Factory's serverless open models made it practical to run many small, specialised agents in parallel and to save the expensive tier for the steps that need it.

### What we built during the submission period
Everything. The repository was created on 2026-10-06 for this hackathon.

---

## Feedback for Nebius & NVIDIA (draft; edit with your real experience)

**Token Factory**
- What worked: the OpenAI-compatible endpoint meant zero SDK lock-in. We call it with plain HTTP and it behaves like other providers, which kept the provider abstraction thin.
- Friction: the price list sits behind the console login. A public machine-readable pricing endpoint (`/v1/models` with per-token prices) would let tools compute cost automatically instead of maintaining a manual table.
- Docs: the structured-output page shows `response_format.json_schema` both as `{name, schema}` and as a raw schema. We implemented both plus a `json_object` fallback. One canonical form, stating which models support strict schemas, would help.
- Request: return `usage` on every response, including streaming, so cost tracking never has to estimate.

**NVIDIA Nemotron**
- The Nano/Super/Ultra family maps naturally onto tiered routing (cheap routine steps vs. hard reasoning). Clear published guidance on which tier supports which context length, tool-calling and JSON-schema strictness would make routing policies easier to configure.

**Nebius AI Cloud**
- S3-compatible Object Storage and Managed PostgreSQL fit our architecture directly. A first-party "deploy a container from the registry to a VM with TLS" quickstart would shorten the path for hackathon teams.

---

## 3-minute video script

| Time | Shot | Voice-over |
|---|---|---|
| 0:00–0:15 | Login page tagline | "Agents are confident, not correct. FORGE makes an agent workflow behave like a program you can inspect, control and verify." |
| 0:15–0:40 | Project → connect GitHub repo → New workflow → Compile with Nemotron | "I describe the goal. Nemotron Ultra on Nebius Token Factory compiles it into a typed graph of specialised agents, validated before I ever see it." |
| 0:40–1:00 | Editor: click a node → model routing, tools, permissions → Approve | "Every node has a contract: model tier, tools, permissions, budget, timeout. The version is immutable once approved." |
| 1:00–1:35 | Run page: graph lights up, parallel scanners, trace streaming, tokens ticking | "Execution is live. Nano, Super and Ultra are routed per step, and three scanners run in parallel. Every model call, tool call and handoff is recorded." |
| 1:35–1:55 | Trigger the README injection run → "ACTION BLOCKED … No changes were made" | "This repo contains a prompt injection. The agent tried to write code; policy blocked it before anything happened." |
| 1:55–2:20 | Findings tab: VERIFIED / REJECTED, expand checks | "Findings are verified against the actual code, the agent's trace and OSV advisories. The fabricated one is rejected." |
| 2:20–2:40 | Approval card with diff → Approve → sandbox tests → report | "Fixes need my approval, are applied only to an isolated copy, and the diff is computed by FORGE, not claimed by the agent." |
| 2:40–2:58 | Replay with a different model → Compare page | "Replay with another model and compare cost, tokens, verification rate and outcome. Don't trust an agent. Verify the workflow." |
