# Resume material

Every number below is **measured**, with the command that produced it. Where a
figure is a projection rather than a measurement, it says so — an interviewer who
catches one inflated number stops believing all of them.

---

## Project title

> **Multi-Agent Research Orchestration System with MCP & HITL Governance
> (AlphaBrief)** — Supervisor-pattern agent system (LangGraph) with an MCP tool
> server, deterministic numeric verification and human-approval gating — solving
> hours of manual daily research with zero tolerance for hallucinated numbers.

**Tech stack line**

> LangGraph, MCP (Model Context Protocol), Claude Sonnet 4.6 + Haiku 4.5,
> FastAPI, yfinance, Next.js 15, Neon Postgres, Langfuse, Docker, GCP Cloud Run,
> Terraform, Vercel Cron

---

## Bullets

> **Automated two years' worth of my own manual morning research into an
> 8-second governed pipeline** by architecting a supervisor-pattern multi-agent
> system (LangGraph, Claude Sonnet 4.6 / Haiku 4.5) in which parallel data, news
> and writer agents consume every tool through a Model Context Protocol server —
> 38 tool calls and 3 model calls per 5-ticker brief, merged race-free through
> typed state reducers.

> **Guaranteed 100% numeric accuracy across 20 evaluated runs** — hallucinated
> figures are impossible by construction, not by prompting: all metrics are
> tool-computed, the brief schema rejects any prose containing a bare numeral,
> and a deterministic verification node recomputes all 35 claims per brief from
> raw price bars using an independent implementation before a human-in-the-loop
> gate (LangGraph interrupts + checkpointer) releases delivery.

> **Achieved 100% end-to-end run success with proven graceful degradation** —
> invalid tickers, dead networks and empty feeds produce flagged partial briefs
> rather than crashes, validated by failure-injection tests that induce each
> fault for real (unknown symbol, unroutable proxy, genuinely empty feed) plus
> two adversarial eval controls that confirm the verifier fires rather than
> rubber-stamping.

> **Held infrastructure to $0 and enforced per-run model spend with a hard
> budget guard** — Haiku-routed supervision, a 15-iteration cap and a token/USD
> ceiling that aborts with `BUDGET_ABORT`; race-free parallel state via reducer
> merges; step-level Langfuse tracing; GCP Cloud Run free tier provisioned with
> Terraform, Vercel Cron scheduling, Neon Postgres persistence.

### Shorter variant (two bullets, for a one-page CV)

> **Built a governed multi-agent research system (LangGraph + MCP) that makes
> hallucinated numbers structurally impossible** — every figure is tool-computed,
> independently recomputed by a deterministic verifier, and human-approved before
> delivery. 100% numeric accuracy across 20 evaluated runs; 190 tests; ruff, mypy
> `--strict`, eslint and tsc all zero-warning.

> **Shipped it end to end on free tiers for $0** — FastAPI + LangGraph + an MCP
> tool server in one container on GCP Cloud Run (Terraform-provisioned), a
> Next.js 15 console with live SSE agent telemetry, Vercel Cron scheduling, Neon
> Postgres and Langfuse tracing.

---

## Measured numbers

| Figure | Value | How it was measured |
| --- | --- | --- |
| Full 5-ticker brief, end to end | **7.8 s** | Cold provider cache, stdio MCP transport, AAPL/MSFT/NVDA/TSLA/AMZN |
| MCP tool calls per brief | **38** | Same run |
| Model calls per brief | **3** | Supervisor + data agent + news agent (deterministic writer) |
| Numeric claims verified per brief | **35 / 35** | Coverage 1.0 |
| Telemetry events per brief | **78** | Streamed over SSE |
| Post-verification numeric accuracy | **100.00%** | `make eval` — 20 runs |
| End-to-end run success rate | **100.0%** | Same |
| Tool-call error rate | **0.00%** | Same |
| Supervisor iterations per brief | **2** | Fan-out, then route to writer |
| Tests | **190** | `pytest -q` |
| Quality gates | **0 warnings** | ruff, ruff-format, mypy `--strict`, pytest (`filterwarnings=error`), eslint, tsc, next build |

### Stated as a projection, not a measurement

**Per-brief Claude spend ≈ $0.05–0.15.** This is a *projection* from published
Haiku 4.5 and Sonnet 4.6 pricing against the measured call pattern (3 model
calls per brief), not a billed figure — the API key arrives after 2026-08-01. The
enforced ceiling is real and tested: `TOKEN_BUDGET_USD` defaults to `$0.50` and
the run aborts with `BUDGET_ABORT` rather than exceed it.

Say it exactly that way in an interview. "Projected from list price against a
measured call pattern; the hard ceiling is enforced and tested" is a stronger
answer than a confident wrong number.

---

## Demo script (90 seconds)

**0:00 — Press RUN.** *(25s)*
Live telemetry streams: the supervisor dispatching both workers in parallel, then
each MCP tool call scrolling with its arguments and millisecond timing —
`get_price_history(ticker=AAPL, days=120) 967ms → 90 daily bars`. Completeness
bars fill per ticker.
> "Two agents in one LangGraph superstep. Every number you're about to see comes
> from a tool call, not from a model."

**0:25 — Verification.** *(15s)*
The ticks cascade green, claim by claim, each showing the stated value beside the
value recomputed from the raw bars.
> "Every number recomputed by code, not trusted from the model — and by a
> different implementation than the one that produced it."

**0:40 — Approve.** *(15s)*
The graph is paused at a checkpointed interrupt. Approve; the brief renders and
lands in email. Hold the phone up.
> "It physically cannot deliver without this click."

**0:55 — The kill shot.** *(20s)*
Switch to **Fault** mode and run again: a dead ticker. The run completes, the
brief is explicitly marked partial, the gap is listed, everything else still
verifies clean. Then **Mismatch** mode: a corrupted figure turns a row red, the
writer regenerates once, and the run lands in `HUMAN_REVIEW` instead of being
delivered.
> "Production is what happens when things fail."

**1:15 — Close.** *(15s)*
> "I did this job manually for two years. Now I govern the system that does it."

---

## Skills block (categorised, as ATS parsers prefer)

**Languages:** Python, TypeScript, SQL

**GenAI / LLM:** Claude (Anthropic API), Prompt Engineering, Structured Outputs
(Tool Use), Model Routing, Hallucination Control, Context Management

**Retrieval / RAG:** RAG Pipelines, Vector Databases (Qdrant), Embeddings
(bge/FastEmbed), Hybrid Retrieval (Dense + BM25), Reciprocal Rank Fusion,
Cross-Encoder Reranking, Chunking Strategies, RAGAS Evaluation

**Agentic AI:** LangGraph, Multi-Agent Systems (Supervisor Pattern), MCP (Model
Context Protocol), Tool Calling, Human-in-the-Loop, Agent Guardrails, Agent
Evaluation

**LLMOps / MLOps:** Langfuse (Observability & Tracing), Evaluation Pipelines,
Latency & Cost Optimization, CI Quality Gates (ruff, mypy, pytest)

**Backend & Data:** FastAPI, REST APIs, Pydantic, PostgreSQL (Neon), SQLAlchemy,
pandas, Anomaly Detection, SSE Streaming

**Cloud & Infra:** GCP (Cloud Run), Docker, Vercel, Terraform (IaC), Hugging Face
Spaces, n8n, GitHub Actions

### ATS terms this project earns honestly

Multi-Agent Systems · LangGraph · MCP (Model Context Protocol) · Agent
Orchestration · Tool Calling · Human-in-the-Loop · Agent Guardrails · Agent
Evaluation · State Management · Observability (Langfuse) · GCP Cloud Run ·
Terraform · Vercel Cron

### Deliberately not claimed

PyTorch, TensorFlow, CUDA, LLM fine-tuning / LoRA, Kubernetes, distributed
training. None are used here. A JD demanding them is an ML-researcher role — a
different target.

---

## Questions this project lets you answer with a story

| Question | The story |
| --- | --- |
| "Tell me about a bug that taught you something." | `RunContext` silently never reached graph nodes once a checkpointer was attached, because LangGraph filters unknown `configurable` keys — that dictionary is checkpoint state. Live handles belong in the runtime context channel. Found by running it, not by reading docs. |
| "How do you prevent hallucinated output?" | Three layers, in order: the model cannot type a number (schema), every number is minted from tool output (claim table), every number is recomputed from raw data by an independent implementation (verifier). Then a human. |
| "How do you know your tests are meaningful?" | The eval ships adversarial controls. A verifier that always passes proves nothing, so two runs deliberately break the system and assert the checks fire. |
| "What did you do about prompt injection?" | Headlines are third-party text: normalised, control-stripped, directive-neutralised, angle-bracket-stripped, length-capped, fenced in `<untrusted_data>`. Agents cannot widen their assigned ticker scope. A risk headline the model paraphrased is dropped unless it matches retrieved text verbatim. Eight-payload corpus in the tests. |
| "How would you make this production-grade?" | It has the shape already — caps, budget guard, DB-level concurrency constraint, structured degradation, tracing, an audit trail. The honest gaps are named in `AUDIT.md`: the SMTP send is unexercised, and no cloud deploy has been applied. |
