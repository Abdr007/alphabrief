# Learning path

Read the codebase in this order. Each file answers an interview question that
actually gets asked about agent systems — the question is stated first, so you
know what you are reading *for*.

Budget: about three focused hours for the six stops, plus an hour reading traces.

---

## 1. `app/graph/state.py` — state design and reducers

> *"Two agents run in parallel and both write to shared state. What stops one
> from clobbering the other?"*

**Read for:** why every field written by more than one node carries an
`Annotated[..., reducer]`, and why the reducers are all *commutative merges*
rather than read-modify-write.

**The answer, in one sentence:** nothing in this system ever reads state, mutates
it, and writes it back — nodes return partial updates and the runtime merges them
with a declared reducer, so ordering between the two workers cannot matter.

**Check yourself:**
- Which fields are written by *both* agents? (`errors`, `tool_calls`,
  `attempts`, `token_spend`, `agents_completed`)
- What happens if you remove the reducer from one of them? (LangGraph raises
  `InvalidUpdateError` on concurrent writes — try it.)
- Why is `iterations` `operator.add` rather than last-write-wins?
- Why are `append_records` and `append_errors` bounded?

**Then read** `tests/test_reducers.py` — the second test drives a real superstep
with `await` points that force interleaving.

---

## 2. `app/graph/supervisor.py` — routing, caps and termination

> *"How do you stop an agent loop from running forever or burning your budget?"*

**Read for:** the two independent brakes, and how the fan-out is expressed.

**The answer:** a hard iteration cap *and* a token/USD budget guard that can trip
from inside any node. They are independent on purpose — a cheap infinite loop is
caught by the first, an expensive short one by the second.

**Check yourself:**
- Why does `route_from_supervisor` return a *list* of node names? (That is what
  puts both workers in one superstep.)
- Where is "retry a failed ticker exactly once" actually implemented?
  (`attempts_for(...) < MAX_ATTEMPTS_PER_TICKER` in the pending-ticker helpers.)
- The supervisor calls Haiku, but the dispatch set is computed from state. Why
  does that matter? (A model hiccup can slow the run; it cannot route it
  somewhere unsafe.)

**Then read** `app/core/budget.py` — note `ensure_headroom`, the pre-flight
check, and why an unknown model prices at the *most expensive* tier.

---

## 3. `app/mcp_server/` — tool schemas, and why MCP

> *"Why MCP? Couldn't you just call a Python function?"*

**Read for:** `registry.py` (the tool contracts and docstrings), `providers.py`
(caching, rate limiting, failure-as-data) and `client.py` (transport, whitelist,
telemetry).

**The answer:** three things a function call does not give you — a *standard*
other agent frameworks and clients can consume, runtime *discoverability* of
schemas and documentation, and a hard *security boundary* around a closed tool
surface.

**Check yourself:**
- Why does every tool return an `error` field instead of raising? (It is what
  makes per-ticker graceful degradation possible at all.)
- Where is the whitelist enforced? (Both ends — server and client.)
- What is the difference between the MCP tools and the "output tools" in
  `prompts.py`? (Data versus structured response. Only the first are executed.)
- Run `make mcp` and speak to the server yourself.

---

## 4. `app/graph/verify.py` + `recompute.py` — determinism

> *"How do you know the model didn't make a number up?"*

**Read for:** the three-layer guarantee, and why `recompute.py` refuses to import
the tool's maths.

**The answer:** the model cannot type a number at all. `app/models/brief.py`
rejects any narrative string containing a bare numeral, so figures exist only as
`{{cN}}` references into a claim table minted from tool output — and every claim
is then recomputed from the raw price bars by a *different implementation*.

**Check yourself:**
- Name the three implementation differences between `metrics.py` and
  `recompute.py`. (Welford vs two-pass variance; `accumulate` vs a running-peak
  loop; `bisect` vs a forward scan.)
- Why is `unverifiable` treated as a failure rather than a pass?
- What are the tolerances, and why is USD `0.005`?
- What happens to a quoted headline the model paraphrased?

**Then read** `tests/test_verifier.py::TestDualPathAgreement` — the proof the two
paths agree on live data, which is what makes the dual check real.

---

## 5. `app/graph/gate.py` — interrupts and checkpointers

> *"How does a graph pause for a human and resume hours later?"*

**Read for:** `interrupt()`, and the comment explaining why the node performs no
side effects before it.

**The answer:** `interrupt()` suspends the graph and checkpoints state. The
process can exit entirely. `ainvoke(Command(resume=decision), config)` continues
from the checkpoint — which is why approval is a separate authenticated HTTP
request and not a callback held in memory.

**Check yourself:**
- Why must the node avoid side effects before `interrupt()`? (It re-executes from
  the top on resume.)
- Where is the "awaiting approval" telemetry emitted instead, and why?
- A reviewer edits the headline to *"stock jumped 42 percent"*. What happens?
  (Rejected — narrative validation still applies to human edits.)
- Which checkpointer is used locally, which in production, and why does the
  serializer have an allowlist?

**Then read** `app/services/runner.py` — the two `ainvoke` calls that bracket the
pause.

---

## 6. `app/graph/context.py` — the LangGraph lesson that cost time

> *"How do you get a live handle — an open MCP session — into a graph node?"*

**The answer:** not through `config["configurable"]`. LangGraph filters unknown
configurable keys once a checkpointer is attached, because that dictionary is
checkpoint state. Live handles belong in the runtime `context` channel
(`StateGraph(..., context_schema=RunContext)` + `ainvoke(..., context=ctx)`),
which is deliberately not persisted.

**Also worth knowing:** a node parameter annotated `RunnableConfig | None` under
`from __future__ import annotations` is a *string* at inspection time, so
LangGraph silently ignores it. The parameter looks right and does nothing.

Both were found by running the thing and reading warnings with `-W error`, not by
reading documentation. That is the story to tell.

---

## 7. Twenty traces, end to end

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`, run `make eval`, then read the
traces rather than the summary.

Look for:
- **Span shape** — is the supervisor being called more often than the number of
  dispatches justifies?
- **Token spend per role** — is Haiku actually taking the routing load?
- **Tool latency** — which provider call dominates? (`get_price_history`, ~1s
  cold, ~0ms cached.)
- **The regeneration path** — run `demo_mismatch` and follow writer → verify →
  writer → verify → gate.
- **A degraded run** — run `demo_fault` and watch the retry-once logic drop a
  dead ticker at attempt two.

"I debug agents by reading traces, not by guessing" is only credible if you can
describe something you actually found this way.

---

## Questions you should be able to answer cold

1. Why LangGraph over CrewAI or AutoGen — and what you give up.
2. What a reducer is, and what breaks without one.
3. Why the tool layer is MCP rather than plain functions.
4. How a hallucinated number is prevented — all three layers, in order.
5. Why the verifier does not import the tool's maths.
6. What happens on a mismatch, exactly, and how many times.
7. How the graph survives the process dying between assembly and approval.
8. Where the run stops if the budget is exhausted mid-flight, and what status it
   reports.
9. How a prompt-injected headline is prevented from redirecting the run.
10. What it costs per brief, and which knob moves that number.

---

## Portfolio context

LedgerLens → Lexora → **AlphaBrief**: extraction, retrieval, agents. One design
language, three live demos, zero dollars spent.
