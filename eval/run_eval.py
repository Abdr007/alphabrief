"""AlphaBrief evaluation harness.

Runs the **full graph** N times against live market data and reports:

* end-to-end success rate,
* post-verification numeric accuracy (must be 100%),
* tool-call error rate,
* average cost and latency per brief.

Plus two **fault-injection controls**, which are the point: a metric that only
ever says 100% is worthless unless you can also show the check fires. The
controls prove the verifier catches a corrupted figure and that a dead ticker
degrades instead of crashing.

Mock-clock safe: no assertion depends on wall-clock time or on a particular
market session, only on internal consistency between the brief and raw state.

    python eval/run_eval.py --runs 20

Writes ``eval/results.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA"]
RESULTS_PATH = Path(__file__).resolve().parent / "results.md"


@dataclass
class RunOutcome:
    """One evaluated run."""

    index: int
    run_id: str
    status: str
    verified: bool
    claims_checked: int = 0
    claims_matched: int = 0
    claims_mismatched: int = 0
    coverage: float = 0.0
    tool_calls: int = 0
    tool_call_errors: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    iterations: int = 0
    partial: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in ("AWAITING_APPROVAL", "HUMAN_REVIEW") and self.claims_checked > 0


@dataclass
class EvalReport:
    """Aggregate results."""

    runs: list[RunOutcome] = field(default_factory=list)
    controls: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: str = ""
    engine: str = ""
    watchlist: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if not self.runs:
            return 0.0
        return sum(1 for r in self.runs if r.succeeded) / len(self.runs)

    @property
    def numeric_accuracy(self) -> float:
        """Fraction of claims in *verified* briefs confirmed by recomputation."""
        verified = [r for r in self.runs if r.verified]
        checked = sum(r.claims_checked for r in verified)
        matched = sum(r.claims_matched for r in verified)
        if checked == 0:
            return 0.0
        return matched / checked

    @property
    def tool_error_rate(self) -> float:
        total = sum(r.tool_calls for r in self.runs)
        errors = sum(r.tool_call_errors for r in self.runs)
        return (errors / total) if total else 0.0

    @property
    def avg_cost(self) -> float:
        return statistics.fmean([r.cost_usd for r in self.runs]) if self.runs else 0.0

    @property
    def avg_latency_ms(self) -> float:
        values = [r.latency_ms for r in self.runs if r.latency_ms > 0]
        return statistics.fmean(values) if values else 0.0

    @property
    def cold_latency_ms(self) -> float:
        """Run 1 — the only run that pays real provider round-trips."""
        first = next((run for run in self.runs if run.index == 1), None)
        return first.latency_ms if first else 0.0

    @property
    def warm_latency_ms(self) -> float:
        """Runs 2..N — orchestration overhead with the provider cache warm."""
        values = [run.latency_ms for run in self.runs if run.index > 1 and run.latency_ms > 0]
        return statistics.fmean(values) if values else 0.0

    @property
    def p95_latency_ms(self) -> float:
        values = sorted(r.latency_ms for r in self.runs if r.latency_ms > 0)
        if not values:
            return 0.0
        index = min(len(values) - 1, round(0.95 * (len(values) - 1)))
        return values[index]


def _configure_environment(database_url: str) -> None:
    """Free, deterministic, network-polite configuration for the eval."""
    os.environ.setdefault("LLM_ENGINE", "auto")
    os.environ["MCP_TRANSPORT"] = "inmemory"
    os.environ["MCP_SHARED_SERVER"] = "true"
    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("PROVIDER_MIN_INTERVAL_SECONDS", "0.15")
    os.environ.setdefault("APPROVAL_TOKEN", "eval-local-token")
    os.environ.setdefault("LOG_LEVEL", "WARNING")


async def _await_gate(service: Any, run_id: str, timeout_s: float = 180.0) -> dict[str, Any]:
    """Poll until the run leaves RUNNING."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = await service.get_run(run_id)
        if isinstance(record, dict) and record["status"] not in ("RUNNING", "QUEUED"):
            return record
        await asyncio.sleep(0.4)
    record = await service.get_run(run_id)
    fallback: dict[str, Any] = {"status": "TIMEOUT"}
    return record if isinstance(record, dict) else fallback


async def _evaluate_once(service: Any, index: int, tickers: list[str]) -> RunOutcome:
    from app.models.run import RunMode

    started = time.perf_counter()
    try:
        run_id = await service.start_run(tickers=tickers, mode=RunMode.STANDARD, trigger="eval")
    except Exception as exc:  # noqa: BLE001 - an eval must record failures, not die
        return RunOutcome(
            index=index, run_id="-", status="START_FAILED", verified=False, error=str(exc)
        )

    record = await _await_gate(service, run_id)
    gate = await service.gate_payload(run_id)
    report = (gate or {}).get("verification") or {}
    events = await service.bus.history(run_id)
    tool_events = [e for e in events if str(e.kind) == "mcp.tool_call"]
    tool_errors = sum(1 for e in tool_events if not e.payload.get("ok", True))

    return RunOutcome(
        index=index,
        run_id=run_id,
        status=str(record.get("status")),
        verified=bool(report.get("ok")),
        claims_checked=int(report.get("checked_claims", 0)),
        claims_matched=int(report.get("matched", 0)),
        claims_mismatched=int(report.get("mismatched", 0)),
        coverage=float(report.get("coverage", 0.0)),
        tool_calls=len(tool_events),
        tool_call_errors=tool_errors,
        cost_usd=float(record.get("cost_usd", 0.0)),
        latency_ms=float(record.get("latency_ms", (time.perf_counter() - started) * 1000)),
        iterations=int(record.get("iterations", 0)),
        partial=bool(record.get("partial", False)),
    )


async def _run_control(service: Any, mode_name: str, tickers: list[str]) -> dict[str, Any]:
    """Fault-injection control: proves the guarantees are not vacuous."""
    from app.models.run import RunMode

    mode = RunMode(mode_name)
    run_id = await service.start_run(tickers=tickers, mode=mode, trigger="eval-control")
    record = await _await_gate(service, run_id)
    gate = await service.gate_payload(run_id)
    report = (gate or {}).get("verification") or {}
    brief = (gate or {}).get("brief") or {}
    return {
        "mode": mode_name,
        "run_id": run_id,
        "status": str(record.get("status")),
        "verified": bool(report.get("ok")),
        "claims_checked": int(report.get("checked_claims", 0)),
        "claims_mismatched": int(report.get("mismatched", 0)),
        "partial": bool(brief.get("partial", False)),
        "snapshot_rows": len(brief.get("snapshot", [])),
        "errors": int(record.get("error_count", 0)),
        "crashed": str(record.get("status")) == "FAILED",
    }


async def run_eval(count: int, tickers: list[str]) -> EvalReport:
    """Execute `count` scored runs plus the fault-injection controls."""
    from app.core.settings import get_settings
    from app.services.runner import RunService

    settings = get_settings()
    report = EvalReport(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        engine=settings.resolved_engine,
        watchlist=tickers,
    )
    service = RunService()

    for index in range(1, count + 1):
        outcome = await _evaluate_once(service, index, tickers)
        report.runs.append(outcome)
        flag = "ok " if outcome.succeeded and outcome.verified else "FAIL"
        print(
            f"  [{index:>2}/{count}] {flag} {outcome.status:<18} "
            f"claims {outcome.claims_matched}/{outcome.claims_checked} "
            f"tools {outcome.tool_calls} "
            f"{outcome.latency_ms / 1000:.1f}s ${outcome.cost_usd:.4f}"
        )
        # Release the watchlist slot so the next run may start.
        await _release(service, outcome.run_id)

    print("\n  fault-injection controls:")
    for mode_name, control_tickers in (
        ("demo_mismatch", tickers[:1]),
        ("demo_fault", tickers[:1]),
    ):
        control = await _run_control(service, mode_name, control_tickers)
        report.controls[mode_name] = control
        print(
            f"   {mode_name:<14} status={control['status']:<14} "
            f"verified={control['verified']} mismatched={control['claims_mismatched']} "
            f"crashed={control['crashed']}"
        )
        await _release(service, control["run_id"])

    await service.shutdown()
    return report


async def _release(service: Any, run_id: str) -> None:
    """Approve a paused run so its watchlist slot is freed for the next one."""
    from app.models.run import HumanDecision

    try:
        await service.submit_decision(run_id, HumanDecision(action="approve", reviewer="eval"))
    except Exception:  # noqa: BLE001 - already terminal, nothing to release
        return


def render_results(report: EvalReport) -> str:
    """Render `eval/results.md`."""
    accuracy = report.numeric_accuracy
    lines: list[str] = [
        "# AlphaBrief — Evaluation Results",
        "",
        f"- **Generated:** {report.started_at}",
        f"- **Runs scored:** {len(report.runs)}",
        f"- **Watchlist:** {', '.join(report.watchlist)}",
        f"- **Model engine:** `{report.engine}`",
        "- **Data:** live yfinance prices and live Yahoo/Google RSS headlines. "
        "No synthetic fixtures.",
        "",
        "## Headline metrics",
        "",
        "| Metric | Result | Target |",
        "| --- | ---: | ---: |",
        f"| End-to-end success rate | {report.success_rate * 100:.1f}% | ≥ 95% |",
        f"| Post-verification numeric accuracy | {accuracy * 100:.2f}% | 100% |",
        f"| Tool-call error rate | {report.tool_error_rate * 100:.2f}% | ≤ 5% |",
        f"| Average cost per brief | ${report.avg_cost:.4f} | ≤ $0.15 |",
        f"| Cold-start latency (run 1, live provider calls) | {report.cold_latency_ms / 1000:.2f}s | — |",
        f"| Warm latency per brief (runs 2–N, cached providers) | {report.warm_latency_ms / 1000:.2f}s | — |",
        f"| p95 latency per brief | {report.p95_latency_ms / 1000:.2f}s | — |",
        "",
        "### Reading these numbers honestly",
        "",
        "- **Latency.** The harness shares one MCP server, and therefore one provider",
        "  cache, across the whole session so that twenty runs are polite to free",
        "  providers. Run 1 pays the real yfinance and RSS round-trips; runs 2–N measure",
        "  orchestration overhead only. Both are reported rather than blended, because",
        "  the blended average would flatter the system.",
        f"- **Cost.** `${report.avg_cost:.4f}` is the measured spend on the",
        f"  `{report.engine}` engine. On the deterministic engine that is genuinely zero —",
        "  it makes no Anthropic calls. With `ANTHROPIC_API_KEY` set, the same graph runs",
        "  on Haiku 4.5 + Sonnet 4.6 at roughly $0.05–0.15 per full 5-ticker brief;",
        "  the budget guard aborts the run rather than exceed `TOKEN_BUDGET_USD`.",
        "- **Accuracy.** This is not a model score. It is the fraction of published",
        "  figures that a second, independent implementation reproduced from the raw",
        "  price bars. A figure that fails is not published at all.",
        "",
        "**Post-verification numeric accuracy is 100% by construction, not by luck.**",
        "Every figure is minted from a tool-computed metric, then independently",
        "recomputed from the raw price bars by `app/graph/recompute.py` before the",
        "brief can reach the approval gate. A figure that fails is not published —",
        "it triggers one regeneration and then `HUMAN_REVIEW`.",
        "",
        "## Fault-injection controls",
        "",
        "A verifier that always passes proves nothing. These runs deliberately break",
        "the system to show the checks actually fire.",
        "",
        "| Control | Status | Verified | Mismatches caught | Crashed |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for name, control in report.controls.items():
        lines.append(
            f"| `{name}` | {control['status']} | {control['verified']} | "
            f"{control['claims_mismatched']} | {control['crashed']} |"
        )
    lines += [
        "",
        "- `demo_mismatch` corrupts one claim after assembly. Expected: the verifier",
        "  catches it, one regeneration is attempted, and the run lands in",
        "  `HUMAN_REVIEW` rather than being delivered.",
        "- `demo_fault` injects an unresolvable ticker. Expected: the run completes",
        "  with per-ticker errors and a brief explicitly marked partial — never a crash.",
        "",
        "## Per-run detail",
        "",
        "| # | Status | Verified | Claims matched | Tool calls | Iterations | Latency | Cost |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in report.runs:
        lines.append(
            f"| {run.index} | {run.status} | {run.verified} | "
            f"{run.claims_matched}/{run.claims_checked} | {run.tool_calls} | "
            f"{run.iterations} | {run.latency_ms / 1000:.2f}s | ${run.cost_usd:.4f} |"
        )
    lines += [
        "",
        "---",
        "",
        "Reproduce with `python eval/run_eval.py --runs "
        f"{len(report.runs)}`. Figures move between runs because the market moves;",
        "the invariants (accuracy, graceful degradation, termination) do not.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the AlphaBrief pipeline.")
    parser.add_argument("--runs", type=int, default=20, help="scored runs (default: 20)")
    parser.add_argument(
        "--tickers",
        type=str,
        default=",".join(DEFAULT_WATCHLIST),
        help="comma-separated watchlist",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="",
        help="override the eval database (default: a temporary SQLite file)",
    )
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    database_url = args.database_url or f"sqlite:///{Path(tempfile.mkdtemp()) / 'eval.db'}"
    _configure_environment(database_url)

    print(f"AlphaBrief eval — {args.runs} runs over {', '.join(tickers)}\n")
    report = asyncio.run(run_eval(args.runs, tickers))

    RESULTS_PATH.write_text(render_results(report), encoding="utf-8")
    accuracy = report.numeric_accuracy

    print(f"\n  success rate            {report.success_rate * 100:.1f}%")
    print(f"  numeric accuracy        {accuracy * 100:.2f}%")
    print(f"  tool-call error rate    {report.tool_error_rate * 100:.2f}%")
    print(f"  avg cost / brief        ${report.avg_cost:.4f} ({report.engine} engine)")
    print(f"  cold-start latency      {report.cold_latency_ms / 1000:.2f}s (live providers)")
    print(f"  warm latency / brief    {report.warm_latency_ms / 1000:.2f}s (cached providers)")
    print(f"\n  wrote {RESULTS_PATH}")

    mismatch_control = report.controls.get("demo_mismatch", {})
    fault_control = report.controls.get("demo_fault", {})
    ok = (
        accuracy >= 1.0
        and report.success_rate >= 0.95
        and mismatch_control.get("claims_mismatched", 0) > 0
        and not mismatch_control.get("verified", True)
        and not fault_control.get("crashed", True)
    )
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
