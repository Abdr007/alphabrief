# AlphaBrief — Audit

Every Definition-of-Done check from the build spec, the command that proves it,
and the result. Where something is **not** fully verified, it says so and why —
an audit that only contains passes is not an audit.

**Audited:** 2026-07-29 · **Commit state:** working tree · **Machine:** macOS,
Python 3.12.13, Node 24.9.0

---

## Summary

| # | Definition of Done | Status |
| --- | --- | --- |
| (a) | ruff + mypy + eslint + tsc clean | ✅ PASS |
| (b) | pytest green, including all six mandated tests | ✅ PASS — 180 tests |
| (c) | e2e: RUN streams telemetry → pauses at gate → approve delivers + archives | ✅ PASS — including a real SMTP send |
| (d) | Verification shows all-green on a clean run, red on injected fault | ✅ PASS |
| (e) | Eval completes with 100% post-verification numeric accuracy | ✅ PASS — 20/20 runs |
| (f) | README with mermaid, MCP docs, local run, free deploy, Terraform | ✅ PASS |

**Open caveats: 0.** E-1 (email never actually sent) was closed on 2026-07-29 with
a real Gmail delivery — see (c). No known correctness, security or reliability
defects are outstanding.

Configuring SMTP exposed one genuine reliability defect, R-1 below, which is now
fixed and pinned by 13 regression tests.

---

## (a) Zero-warning quality gates

```
$ python -m ruff check .
All checks passed!

$ python -m ruff format --check .
66 files already formatted

$ python -m mypy
Success: no issues found in 59 source files

$ cd apps/web && npx tsc --noEmit
(no output)

$ cd apps/web && npx eslint .
(no output, exit 0)

$ cd apps/web && npm run build
✓ Compiled successfully
✓ Generating static pages (3/3)
```

Notes on rigour, so the "clean" is meaningful rather than configured away:

- **mypy runs `--strict`** over `apps/api/app`, `apps/api/tests` *and* `eval/`.
- **ruff enables `BLE` (blind-except)**, so every broad `except Exception` must
  either log with `exc_info` or carry a written justification. It also enables
  `S` (bandit), `ASYNC`, `DTZ` (timezone-aware datetimes), `PTH`, `T20` (no stray
  `print`) and a pylint subset.
- **pytest runs with `filterwarnings = error`.** A `DeprecationWarning` fails the
  suite. This is how two real deprecations were caught during the build
  (`HTTP_422_UNPROCESSABLE_ENTITY`, and LangGraph's checkpoint-deserialisation
  warning).
- **ESLint bans `any`** and warns on stray `console`.

### Defects found and fixed by these gates

| Defect | How it surfaced | Fix |
| --- | --- | --- |
| `fetch_rss_news` recursed into itself — the MCP tool shadowed the provider function of the same name, so the tool always errored | Live MCP round-trip test | Namespaced provider calls (`providers.fetch_rss_news`) and a comment forbidding unqualified calls in that scope |
| An unknown ticker reported `ok` fundamentals — yfinance returns a non-empty dict of residual keys for symbols that do not exist | Live fake-ticker probe | Require at least one identifying field before treating a payload as usable |
| `RunContext` never reached graph nodes once a checkpointer was attached — LangGraph filters unknown `config["configurable"]` keys | Full graph run failed on the first node | Moved runtime dependencies to LangGraph's `context_schema` / `Runtime.context` channel |
| Node `config` parameters were silently ignored — under `from __future__ import annotations` the annotation is a string, which LangGraph cannot match | `UserWarning` with `-W error` | Removed the dead parameter; nodes resolve context via `get_runtime()`, with a ContextVar override for direct unit invocation |
| Checkpoint deserialisation warned it would be blocked in a future version | `-W error::DeprecationWarning` | Strict serializer with an explicit allowlist of AlphaBrief's own models (also narrows the deserialisation surface) |
| Re-archiving a run's telemetry raised `IntegrityError`, silently losing the audit trail | `test_events_are_archived_idempotently` | `(run_id, seq)` made the primary key so `merge()` is genuinely idempotent |
| Brief prose read `"moving +0.25% percent"` and `"headlines carried no directional language"` | Reading real rendered output | Removed the duplicated unit; made the sentiment reasoning numeral-free by construction |

---

## (b) Test suite

```
$ cd apps/api && python -m pytest tests/ -q
180 passed
```

| File | Tests | Covers |
| --- | ---: | --- |
| `test_security.py` | 29 | Prompt-injection corpus, auth, tool whitelist, scope containment |
| `test_api.py` | 27 | HTTP contracts, hostile input, headers, rate limiting |
| `test_brief_schema.py` | 25 | The no-bare-numerals guarantee, claim integrity, rendering |
| `test_verifier.py` | 20 | Dual-path metric agreement, mismatch detection, regeneration routing |
| `test_degradation.py` | 12 | Fake ticker, dead network, empty RSS |
| `test_mcp_server.py` | 12 | Tool discovery, live calls, caching, rate limiting, bounds |
| `test_restart_recovery.py` | 13 | Orphan reconciliation, un-resumable approvals, SMTP kept out of the suite |
| `test_integration.py` | 11 | Full chain, human gate, fault-injection modes, concurrency |
| `test_repository.py` | 11 | Transactions, the active-run constraint, archival |
| `test_loop_safety.py` | 10 | Iteration cap, forced-loop termination, budget guard |
| `test_reducers.py` | 10 | Reducer algebra + a real parallel superstep |

### The six mandated tests

| Mandated test | Where | What it actually does |
| --- | --- | --- |
| **Loop-cap termination** | `test_loop_safety.py::test_forced_loop_terminates` | Builds a real graph whose workers never complete a ticker, so the watchlist can never finish. Asserts the run stops at the cap with `ITERATION_ABORT` rather than spinning. |
| **Parallel reducer-merge** | `test_reducers.py::test_both_agents_writes_survive_one_superstep` | Two nodes write six shared channels concurrently in one LangGraph superstep, with `await` points forcing interleaving. Asserts neither side clobbered the other on **any** channel. |
| **Verifier mismatch → regeneration** | `test_integration.py::test_demo_mismatch_is_caught_and_escalated` | Runs the full graph with a deliberately corrupted claim. Asserts exactly two writer passes and two verify passes, then `HUMAN_REVIEW` — never delivered. |
| **Fake ticker** | `test_degradation.py::TestFakeTicker` (5 tests) | Requests a symbol no exchange lists, from the live provider. Asserts structured errors, a partial brief that is explicitly flagged, and that the partial brief still verifies. |
| **Dead network** | `test_degradation.py::TestDeadNetwork` (4 tests) | Points every proxy variable at an unroutable loopback port, producing genuine connection failures. Asserts prices, fundamentals and news all degrade, and a totally dead run still yields a valid, marked brief. |
| **Empty RSS** | `test_degradation.py::TestEmptyRss` (3 tests) | A real feed that legitimately returns zero items. Asserts empty ≠ error, sentiment is neutral with stated reasoning, and the brief says so rather than inventing a read. |

### Test-data policy

Metric, verification and degradation tests run against **live market data**
fetched once per session, not against invented fixtures. Only *structurally
degenerate* inputs (zero bars, one bar) are hand-constructed, because a working
provider cannot produce those shapes — and they are exactly what the degradation
paths must survive. With no network the live fixtures **skip** with a clear
message rather than silently passing on fake data.

---

## (c) End-to-end

Verified against a running API (`:7871`) and a production build of the console
(`:3021`), driving the console's own route handlers — not the API directly.

```
1. POST /api/run                      → 202, run_e2f9a278c71a4dbf8c5d
2. GET  /api/stream/{id}   (SSE)      → live telemetry, e.g.
     event: supervisor.plan  "Dispatching data_agent + news_agent (parallel)"
     event: mcp.tool_call    get_price_history(ticker=AAPL, days=120) 967ms → 90 daily bars
     event: mcp.tool_call    get_fundamentals(ticker=AAPL)           307ms → Apple Inc. · P/E 41.33
     event: mcp.tool_call    compute_metrics(ticker=AAPL, bars=[90 items]) 4ms
3. Run pauses                          → status AWAITING_APPROVAL
4. GET  /api/gate/{id}                 → verified: True | claims 14/14 | coverage 1.0
5. POST /v1/runs/{id}/decision         → 401  (no token: approval is not reachable
                                              from a browser)
6. POST /api/decision/{id}             → {"status": "DELIVERED"}  (route injects the
                                              server-side token)
7. GET  /v1/runs/{id}                  → DELIVERED · verified · 2 iterations ·
                                          15 tool calls · 3578 ms
8. GET  /api/archive                   → the run, with headline and status
```

Console markup was verified to contain the wordmark, the RUN button, the three
run modes, the telemetry panel, the five-step governance chain, and the archive
route.

### E-1 — CLOSED 2026-07-29: the email is really sent

A Gmail app password was configured and the whole chain was re-run on the live
stack (API `:7877`, console `:3001`) — `POST /api/run` for `NVDA, AAPL`, pause at
`AWAITING_APPROVAL`, then `POST /api/decision/{id}` returning
`{"status":"DELIVERED"}`. The server recorded:

```
gate.awaiting     Awaiting human approval
gate.decision     Human approved the brief
delivery.sent     delivered to 1 recipient(s) via smtp.gmail.com
```

The message was sent by `app/services/email.py` (MIME multipart: Markdown body
plus an HTML alternative) against a real archived brief whose 14 figures had all
been independently recomputed. Delivery is still fail-soft: an SMTP outage is
logged and reported, and never fails a run the human already approved.

Because `Settings` reads `../../.env`, a working credential on an operator's
machine would otherwise have been picked up by the test suite — whose
integration tests approve briefs — and mailed a real inbox on every run.
`tests/conftest.py` now blanks `SMTP_*` for the suite, and two tests pin that.

---

## (d) Verification screen: green and red

| Mode | Result |
| --- | --- |
| `standard` | `verified: True`, 14/14 claims matched, coverage 1.0 — every row ticks green |
| `demo_mismatch` | `HUMAN_REVIEW`, 6/7 matched, **1 mismatch**: `MSFT.last_close` claimed `402.465` vs recomputed `394.695`, Δ `7.77` against a tolerance of `0.005` — the row renders red |
| `demo_fault` | `AWAITING_APPROVAL`, partial brief: `ZZZZQQQQ` appears in the watchlist but not the snapshot, data gaps listed, everything that resolved still verified clean |

The mismatch run shows **two writer passes and two verify passes** in the event
log: the mismatch triggered exactly one regeneration, and only then escalated.

---

## (e) Evaluation

```
$ python eval/run_eval.py --runs 20 --tickers AAPL,MSFT,NVDA

  success rate            100.0%
  numeric accuracy        100.00%
  tool-call error rate    0.00%
  avg cost / brief        $0.0000 (deterministic engine)
  cold-start latency      4.42s (live providers)
  warm latency / brief    0.03s (cached providers)

  RESULT: PASS
```

Full report: [`eval/results.md`](eval/results.md).

The eval includes **fault-injection controls**, because a metric that always
reads 100% proves nothing on its own:

| Control | Expected | Observed |
| --- | --- | --- |
| `demo_mismatch` | verifier catches it, run escalates | `HUMAN_REVIEW`, 1 mismatch caught, not delivered |
| `demo_fault` | degrades, never crashes | `AWAITING_APPROVAL`, partial brief, `crashed=False` |

Latency is reported cold and warm separately rather than blended, because the
harness shares one provider cache across the session to be polite to free
providers — a blended average would flatter the system.

---

## (f) Documentation

| Requirement | Where |
| --- | --- |
| Mermaid graph diagram | `README.md` (and `make diagram` regenerates it from the real `StateGraph`) |
| MCP tool docs | `README.md` § *The MCP tool server* — signatures, returns, published metric definitions |
| Local run | `README.md` § *Running it locally* |
| Vercel (web + cron) | `README.md` § *Free deployment map*, `apps/web/vercel.json` |
| GCP Cloud Run with gcloud commands | `README.md` § *GCP Cloud Run (recommended)* |
| Hugging Face Spaces fallback | `README.md`, `apps/api/Dockerfile` (port 7860, uid 1000) |
| Neon, Langfuse, Gmail SMTP | `README.md` § *Neon, Langfuse, Gmail* |
| Optional Terraform module | `infra/terraform/` — Cloud Run v2, Artifact Registry, Secret Manager, least-privilege SA |
| Production upgrade path | `README.md` § *Production upgrade path* |

### Container verified, not just written

```
$ docker build -t alphabrief-api -f apps/api/Dockerfile apps/api
Successfully tagged alphabrief-api:latest        (741 MB)

$ docker run -d -p 7893:7860 alphabrief-api
$ curl http://127.0.0.1:7893/health
{"status":"ok","engine":"deterministic","mcp_transport":"stdio", ...}

$ curl http://127.0.0.1:7893/v1/mcp/tools
tools advertised: compute_metrics, fetch_rss_news, get_fundamentals, get_price_history
doc lengths:      1250, 682, 560, 668 characters

$ docker exec alphabrief-check id
uid=1000(alphabrief) gid=1000(alphabrief)
```

This proves the three things the deployment story depends on: the API, the
LangGraph orchestrator and the MCP tool server all run **in one container**; the
MCP server is genuinely spoken to over **stdio inside** that container; and the
process runs as **uid 1000**, which is what Hugging Face Spaces requires. Cloud
Run additionally honours its injected `$PORT`.

---

## Security posture

| Control | Implementation | Test |
| --- | --- | --- |
| Tool whitelist | Closed set of four tools, enforced server- and client-side | `test_security.py::TestToolWhitelist` (10) |
| No arbitrary execution | No `run_python`, `eval`, shell or filesystem tool exists to call | Whitelist tests |
| Prompt-injection containment | Untrusted text is NFKC-normalised, control-stripped, directive-neutralised, angle-brackets removed, length-capped and fenced in `<untrusted_data>` | 8-payload corpus + fence-escape test |
| Agent scope containment | A model may narrow but never widen its assigned tickers | `test_data_agent_cannot_widen_its_assignment` |
| Risk-event grounding | A risk headline the model paraphrased is dropped unless it matches retrieved text verbatim | `news_agent._match_headline`, verifier quote checks |
| Secrets via env only | No default token ships; an unset token generates a random per-process value | `test_no_default_token_is_ever_shipped` |
| Approval auth | Constant-time bearer comparison on every mutating endpoint | `test_api.py::TestApprovalAuth` (4) |
| Token never in the browser | Every call proxied through Next route handlers; no `NEXT_PUBLIC_*` API variable exists | Verified in the e2e run (step 5 vs 6) |
| Request-size cap | ASGI-level limit on declared *and* streamed bytes | `test_oversized_body_is_rejected` |
| Rate limiting | Sliding window per client on state-changing methods, bounded memory | `test_repeated_writes_are_throttled` |
| Security headers | CSP `default-src 'none'`, `X-Frame-Options: DENY`, nosniff, no-referrer, Permissions-Policy | `test_api.py::TestSecurityHeaders` |
| Checkpoint deserialisation | Strict allowlist of AlphaBrief's own models | `app/services/serde.py` |
| Non-leaking errors | Unhandled exceptions log server-side, return an opaque 500 | `main.py` exception handler |
| Input validation | Ticker charset/length, watchlist size, raw-list bound, unknown fields rejected | `test_api.py::TestInputValidation` (11) |
| Container hardening | Non-root uid 1000, no build toolchain in the runtime layer, healthcheck | `apps/api/Dockerfile` |

---

## Reliability posture

| Risk | Mitigation |
| --- | --- |
| Infinite agent loop | Hard iteration cap **and** an independent budget guard; a ticker gets exactly one retry |
| Runaway spend | Pre-flight headroom check before each call, plus post-call charge; unknown models price at the most expensive tier so spend is never under-reported |
| Lost updates under parallelism | Every shared channel has a commutative reducer; no read-modify-write anywhere |
| Duplicate concurrent runs | Partial unique index in the database |
| Provider outage | Structured per-item errors; the run degrades to a flagged partial brief |
| Slow SSE client | Unbounded per-subscriber queues so a slow reader cannot stall the graph; connection budget caps subscribers |
| Unbounded memory | Event backlog, tool-call records, error list and rate-limit buckets are all capped |
| Wedged run | Wall-clock timeout on the whole graph invocation |
| Run abandoned by a restart | Boot-time reconciliation closes it and frees the watchlist (R-1) |
| Approving a run whose checkpoint is gone | Refused with `409` and an explanation, never silently re-executed (R-1) |
| Tracing outage | Langfuse is best-effort; every call is wrapped and can never fail a run |
| Mail outage | Delivery failure is recorded as a warning on an already-approved run |

---

## R-1 — restart left a watchlist permanently un-runnable (HIGH, fixed)

Found while restarting the API to pick up the new SMTP credential, then
reproduced deliberately before any fix was written.

**Two distinct faults, one cause.** Four statuses — `QUEUED`, `RUNNING`,
`AWAITING_APPROVAL`, `HUMAN_REVIEW` — occupy a watchlist's single active-run slot
in the partial unique index. Nothing moved a run *inherited from a dead process*
out of that set.

1. **Permanent wedge.** A run interrupted mid-graph stayed `RUNNING` forever, and
   every later attempt on that watchlist was rejected with *"a run is already
   active"*. There was no recovery short of hand-editing the database. This API
   was killed mid-run several times during development, so the default five-ticker
   watchlist was one crash away from being permanently dead in a demo.

2. **Silent destruction on approve.** `Command(resume=...)` against a thread the
   checkpointer no longer holds does **not** raise. LangGraph starts a *fresh*
   execution that never reaches the gate; it returned `status: None`, and the run
   was then marked `FAILED`. Clicking Approve destroyed the run and reported
   HTTP 200.

**Fix.** `RunService.reconcile_orphans()` runs in the FastAPI lifespan. A run is
an orphan when this process is not executing it and it cannot be resumed:
`QUEUED`/`RUNNING` always qualify, because nothing resumes mid-graph work;
`AWAITING_APPROVAL`/`HUMAN_REVIEW` qualify only when no checkpoint survives — so
with Postgres a paused gate is correctly *preserved* across a restart, which is
what the checkpointer was always for. Orphans are closed as `FAILED` with an
`abort_reason` the archive displays. Separately, `submit_decision` now verifies
the thread exists and raises `RunNotResumableError` → **409** with a message
pointing at the still-archived brief, instead of re-running the graph.

Pinned by `tests/test_restart_recovery.py` (13 tests), including that a genuinely
in-flight run is never reaped and that a durable checkpoint at the gate survives.

Observed on the live server immediately after the restart:

```
LangGraph checkpointer: InMemorySaver (paused runs end at shutdown)
run run_ca929fb2c99c46258178 closed as orphaned (was AWAITING_APPROVAL)
reconciled 1 orphaned run(s) from a previous process
```

---

## What is deliberately not claimed

- **Paused runs do not survive a restart on SQLite.** The in-memory checkpointer
  is volatile by nature; a run waiting at the gate is closed by reconciliation
  rather than resumed (R-1). Pointing `DATABASE_URL` at Postgres switches to
  `PostgresSaver` and the gate then survives — that branch is covered by tests
  but has not been exercised against a real Postgres instance.
- **Cloud Run and Vercel deployment have not been executed.** The container is
  built and verified running locally (above), but the Terraform module and
  gcloud commands are written and reviewed rather than applied — that needs a
  GCP project and a card on file.
- **No Claude API call has been made.** The key arrives after 2026-08-01. Every
  result above is from the deterministic engine, which drives the identical
  graph, MCP tools and verifier — the model-routing code path
  (`AnthropicEngine`) is written against the documented SDK surface but is
  unexercised until the key exists.
- **PyTorch, TensorFlow, CUDA, fine-tuning, Kubernetes and distributed training
  are not used and are not claimed anywhere.**
