# AlphaBrief — Evaluation Results

- **Generated:** 2026-07-29T22:01:13+00:00
- **Runs scored:** 20
- **Watchlist:** AAPL, MSFT, NVDA
- **Model engine:** `deterministic`
- **Data:** live yfinance prices and live Yahoo/Google RSS headlines. No synthetic fixtures.

## Headline metrics

| Metric | Result | Target |
| --- | ---: | ---: |
| End-to-end success rate | 100.0% | ≥ 95% |
| Post-verification numeric accuracy | 100.00% | 100% |
| Tool-call error rate | 0.00% | ≤ 5% |
| Average cost per brief | $0.0000 | ≤ $0.15 |
| Cold-start latency (run 1, live provider calls) | 4.29s | — |
| Warm latency per brief (runs 2–N, cached providers) | 0.03s | — |
| p95 latency per brief | 0.03s | — |

### Reading these numbers honestly

- **Latency.** The harness shares one MCP server, and therefore one provider
  cache, across the whole session so that twenty runs are polite to free
  providers. Run 1 pays the real yfinance and RSS round-trips; runs 2–N measure
  orchestration overhead only. Both are reported rather than blended, because
  the blended average would flatter the system.
- **Cost.** `$0.0000` is the measured spend on the
  `deterministic` engine. On the deterministic engine that is genuinely zero —
  it makes no Anthropic calls. With `ANTHROPIC_API_KEY` set, the same graph runs
  on Haiku 4.5 + Sonnet 4.6 at roughly $0.05–0.15 per full 5-ticker brief;
  the budget guard aborts the run rather than exceed `TOKEN_BUDGET_USD`.
- **Accuracy.** This is not a model score. It is the fraction of published
  figures that a second, independent implementation reproduced from the raw
  price bars. A figure that fails is not published at all.

**Post-verification numeric accuracy is 100% by construction, not by luck.**
Every figure is minted from a tool-computed metric, then independently
recomputed from the raw price bars by `app/graph/recompute.py` before the
brief can reach the approval gate. A figure that fails is not published —
it triggers one regeneration and then `HUMAN_REVIEW`.

## Fault-injection controls

A verifier that always passes proves nothing. These runs deliberately break
the system to show the checks actually fire.

| Control | Status | Verified | Mismatches caught | Crashed |
| --- | --- | --- | ---: | --- |
| `demo_mismatch` | HUMAN_REVIEW | False | 1 | False |
| `demo_fault` | AWAITING_APPROVAL | True | 0 | False |

- `demo_mismatch` corrupts one claim after assembly. Expected: the verifier
  catches it, one regeneration is attempted, and the run lands in
  `HUMAN_REVIEW` rather than being delivered.
- `demo_fault` injects an unresolvable ticker. Expected: the run completes
  with per-ticker errors and a brief explicitly marked partial — never a crash.

## Per-run detail

| # | Status | Verified | Claims matched | Tool calls | Iterations | Latency | Cost |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 4.29s | $0.0000 |
| 2 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 3 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 4 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 5 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 6 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 7 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 8 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 9 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 10 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 11 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 12 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 13 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 14 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 15 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 16 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 17 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 18 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 19 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |
| 20 | AWAITING_APPROVAL | True | 21/21 | 12 | 2 | 0.03s | $0.0000 |

---

Reproduce with `python eval/run_eval.py --runs 20`. Figures move between runs because the market moves;
the invariants (accuracy, graceful degradation, termination) do not.
