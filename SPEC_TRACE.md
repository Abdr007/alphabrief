# Spec traceability

Every requirement in the build spec → the file that implements it → the test that
holds it. Section numbers refer to the *Final Build Spec*.

Legend: **✅** implemented and tested · **📄** documentation deliverable ·
**⚙️** infrastructure, written but not applied (needs the user's own cloud account)

---

## §1–2 · Product and architecture

| # | Requirement | Implementation | Test |
| --- | --- | --- | --- |
| R1 | Product codename & UI logo: AlphaBrief | `apps/web/components/Chrome.tsx` | markup check (AUDIT c) |
| R3 | Demo URL `alphabrief.vercel.app` | `README.md`, `apps/web/vercel.json` | 📄 |
| R4 | `$0` total cost — yfinance + RSS keyless, Claude only | `app/mcp_server/providers.py` | `test_mcp_server.py` |
| R5 | 3 worker agents + supervisor, 5-ticker watchlist | `app/graph/build.py`, `DEFAULT_WATCHLIST` | `test_integration.py` |
| R6 | Wow moment: RUN → live feed → green ticks → gate → brief | `apps/web/components/RunConsole.tsx` | AUDIT (c), (d) |
| R7 | Trigger: Vercel Cron weekdays 07:00 **or** the RUN button | `apps/web/app/api/trigger/route.ts`, `vercel.json` | ✅ route + constant-time cron auth |
| R8 | Supervisor (Haiku 4.5): plans, parallel fan-out, retries once, hard cap 15 + token budget | `app/graph/supervisor.py` | `test_loop_safety.py` (10) |
| R9 | Data agent (Sonnet): `price_history`, `fundamentals`, `compute_metrics` | `app/graph/data_agent.py` | `test_integration.py`, `test_mcp_server.py` |
| R10 | News agent (Sonnet): `fetch_rss_news` → summarise, sentiment −1..1 with reasoning, risk events | `app/graph/news_agent.py` | `test_degradation.py`, `test_security.py` |
| R11 | MCP tool server — standardised, discoverable, reusable by any framework | `app/mcp_server/` + `GET /v1/mcp/tools` | `test_mcp_server.py::TestDiscoverability` |
| R12 | Shared state typed, reducer-merged, no lost updates | `app/graph/state.py` | `test_reducers.py` (10) |
| R13 | Writer (Sonnet 4.6): Pydantic-enforced brief; assembles only, computes nothing | `app/graph/writer.py`, `app/models/brief.py` | `test_brief_schema.py` (25) |
| R14 | Verification node — deterministic, never an LLM; mismatch → one regeneration else HUMAN_REVIEW | `app/graph/verify.py`, `app/graph/recompute.py` | `test_verifier.py` (20) |
| R15 | HITL gate — LangGraph interrupt + checkpointer; Approve/Edit/Reject; never auto-sends | `app/graph/gate.py` | `test_integration.py::TestHumanGate` (5) |
| R16 | Delivery & archive — SMTP email + Neon Postgres + Langfuse trace | `app/services/email.py`, `repository.py`, `core/tracing.py` | ✅ (email send: caveat E-1) |
| R17 | Signature sentence present in the product | `README.md`, `apps/web/components/Chrome.tsx` | markup check |

---

## §3 · Tech stack

| # | Requirement | Implementation |
| --- | --- | --- |
| R18 | LangGraph: typed StateGraph + checkpointer + interrupts; CrewAI/AutoGen named as alternatives | `app/graph/build.py`; README § *Design decisions* |
| R19 | MCP server (Python SDK) wrapping yfinance/RSS/metrics | `app/mcp_server/registry.py` |
| R20 | Model routing by task weight: Haiku supervisor, Sonnet workers/writer | `app/core/claude.py`, `core/settings.py` |
| R21 | yfinance market data + stated production upgrade path | `providers.py`; README § *Production upgrade path* |
| R22 | RSS via feedparser (Yahoo Finance + Google News) | `providers.py` |
| R23 | Verification: pure Python + Pydantic-parsed brief | `verify.py`, `recompute.py` |
| R24 | Neon Postgres (runs, briefs, approvals) + LangGraph checkpointer | `app/models/db.py`, `services/runner.py` |
| R25 | Langfuse traces of every agent step + token spend | `app/core/tracing.py`, `graph/llm.py` |
| R26 | Next.js 15, dark design system, live SSE feed | `apps/web/` |
| R27 | Vercel Cron; n8n named as the alternative used in LedgerLens | `vercel.json`; README § *Design decisions* |

---

## §4 · Zero-cost deployment

| # | Requirement | Where | Status |
| --- | --- | --- | --- |
| R28 | HF Spaces Docker: FastAPI + LangGraph + MCP in one container, port 7860 | `apps/api/Dockerfile` | ⚙️ image builds as uid 1000 on 7860 |
| R29 | Vercel Hobby: UI + one-a-day cron | `vercel.json` | ⚙️ |
| R31 | Claude cost ~$0.05–0.15/brief via Haiku routing + caps | `settings.py`, `budget.py` | ✅ guard tested |
| R32 | Cache pulls per run; polite rate limits | `providers.py` `ProviderContext` | `test_mcp_server.py::TestCachingAndRateLimiting` |
| R33 | GCP Cloud Run as primary host, with gcloud commands | README; `infra/terraform/` | ⚙️ |
| R34 | AWS/Azure verdicts documented | README § *Free deployment map* | 📄 |
| R35 | HF Spaces as the no-card fallback | README; Dockerfile | 📄 |
| R36 | Optional Terraform module: Cloud Run service + secrets | `infra/terraform/{main,variables,outputs}.tf` | ⚙️ |

---

## §5 · Repository layout

Matches the spec exactly: `apps/web`, `apps/api/app/{graph,mcp_server,core}`,
`tests/`, `eval/run_eval.py`, `infra/`, `AUDIT.md`, `README.md`, `.env.example`.

`graph/` contains the specified `state.py`, `supervisor.py`, `data_agent.py`,
`news_agent.py`, `writer.py`, `verify.py`, `gate.py` — plus `recompute.py`
(the verifier's independent maths), `compose.py`, `deliver.py`, `build.py`,
`context.py`, `llm.py` and `prompts.py`.

---

## §6 · UI

| Requirement | Implementation |
| --- | --- |
| Dark, high-contrast design system | `apps/web/app/globals.css` `@theme` tokens — **amber phosphor terminal**, see the deviation below |
| Large glowing RUN button | `RunConsole.tsx` — framed terminal key, amber bloom, pulse |
| SSE feed showing dispatches and each MCP tool call with args + timing | `TelemetryFeed.tsx` (verified live: `get_price_history(ticker=AAPL, days=120) 967ms`) |
| Per-ticker completeness bars | `CompletenessRail.tsx` |
| Verification screen, animated per-claim green ticks / red mismatch | `VerificationPanel.tsx` |
| Approval gate: Approve / Edit / Reject resuming the graph | `ApprovalGate.tsx` |
| Archive with cost, latency, iterations per run | `app/archive/page.tsx` |
| `vercel.json` weekday 07:00 cron → `/api/trigger` | `vercel.json` |

---

## §7 · Production quality bar

| Requirement | Implementation | Test |
| --- | --- | --- |
| Hard cap 15 + budget guard aborting with a clear status | `supervisor.py`, `core/budget.py` | `test_forced_loop_terminates`, `test_run_aborts_with_budget_status` |
| Parallel writes merged via reducers | `state.py` | `test_both_agents_writes_survive_one_superstep` |
| DB writes transactional | `services/repository.py` | `test_repository.py` |
| One active run per watchlist by DB constraint | `models/db.py` partial unique index | `TestActiveRunConstraint` (4) |
| Fake ticker / dead network / empty RSS → partial brief, never a crash | `providers.py`, agents, `compose.py` | `test_degradation.py` (12) |
| Verifier recomputes 100% of claims; mismatch → one regeneration then HUMAN_REVIEW | `verify.py` | `test_verifier.py`, `test_demo_mismatch_is_caught_and_escalated` |
| Eval asserts 100% post-verification accuracy over 20 runs | `eval/run_eval.py` | `eval/results.md` |
| MCP tools whitelisted; no arbitrary execution | `registry.py`, `client.py` | `TestToolWhitelist` (10) |
| News text treated as data (injection-safe prompts) | `core/security.py`, `prompts.py` | 8-payload corpus |
| Secrets via env only | `core/settings.py` | `test_no_default_token_is_ever_shipped` |
| Approval endpoints require an auth token | `core/security.py`, `api/routes.py` | `TestApprovalAuth` (4) |
| ruff + mypy + eslint + tsc clean; pytest green; no browser console errors | — | AUDIT (a), (b) |

---

## §8 · Master prompt specifics

| Requirement | Status |
| --- | --- |
| Default watchlist AAPL, MSFT, NVDA, TSLA, AMZN — editable | ✅ `DEFAULT_WATCHLIST`, editable in the console |
| `get_price_history(ticker, days)` | ✅ |
| `get_fundamentals(ticker)` | ✅ |
| `compute_metrics(prices) → {return_30d, volatility, max_drawdown, pe}` | ✅ plus `last_close`, `previous_close`, `change_1d_pct`, and the 30-day baseline date |
| `fetch_rss_news(ticker, limit)` via feedparser | ✅ |
| Typed args, rich docstrings, per-run cache, polite rate limiting | ✅ `test_mcp_server.py` asserts every tool has >120 chars of documentation |
| Agents call tools for ALL numbers; writer computes nothing | ✅ enforced by the schema, not by the prompt |
| State TypedDict with reducer-merged fields | ✅ |
| Retry failed tickers once; cap 15; `BUDGET_ABORT` on breach | ✅ |
| FastAPI 3.12, langgraph, anthropic, mcp, yfinance, feedparser, SQLAlchemy/Neon, Langfuse, auth token, Dockerfile 7860 | ✅ |
| Next.js 15 TS Tailwind Framer Motion; dark tokens; full console | ✅ |
| `eval/run_eval.py`: 20 runs, mock-clock safe, all four metrics, writes `results.md` | ✅ |
| Definition of Done (a)–(f) + `AUDIT.md` with every check PASS | ✅ `AUDIT.md` |

## Deviations, stated plainly

**1. Visual identity — the spec's shared design system was overridden.**

Spec §6 asks for "dark `#0a0e1a`, cyan `#22d3ee`, glass" and §3 justifies it:
"your three demos look like one product family — deliberate, professional."

In practice the two sibling demos had landed on *identical* tokens:

| Project | Base | Accent |
| --- | --- | --- |
| LedgerLens | `#0a0e1a` | `#22d3ee` |
| Lexora | `#0a0e1a` | `#22d3ee` |

A third demo on the same palette reads as one template rendered three times,
which costs more credibility than family resemblance buys. On the project
owner's direction, AlphaBrief was rebuilt with its own identity:

| | AlphaBrief |
| --- | --- |
| Base | `#08090b` true black |
| Primary | `#ffb454` amber phosphor |
| Verified | `#35d07f` phosphor green |
| Alert | `#ff5f56` |
| Type | monospace throughout, tabular numerals |
| Motif | trading-floor terminal: ruled columns, CRT scanlines, corner ticks, block meters |

The choice is also thematically right: this is a market instrument, and the
terminal language says so before a word is read. Everything else in §6 — the
glowing RUN key, the SSE feed with per-tool args and timings, per-ticker
completeness meters, the animated verification pass, the Approve/Edit/Reject
gate, the archive with cost/latency/iterations — is implemented as specified.

**2. shadcn/ui not used.** The console uses hand-written components on the same
Tailwind token system. shadcn adds a component registry and Radix dependencies
for a surface that is one button, one textarea and three tables; the terminal
aesthetic is bespoke either way. Tailwind and the dark token system are present
as specified.

---

## §9–10 · Resume, demo script, learning path

| Requirement | Where |
| --- | --- |
| Resume bullets with real numbers | `RESUME.md` |
| 90-second demo script | `RESUME.md` § *Demo script* |
| `demo-fault` mode with a fake ticker | `RunMode.DEMO_FAULT` — plus `DEMO_MISMATCH` for the verifier |
| Learning path: state → supervisor → mcp_server → verify → gate → traces | `LEARNING.md` |
| ATS keyword coverage + categorised skills block | `RESUME.md` § *Skills* |
| Terms not claimed (PyTorch, CUDA, Kubernetes, …) | `RESUME.md`, `AUDIT.md` |
