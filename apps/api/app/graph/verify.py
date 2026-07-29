"""The verification node — deterministic, never an LLM.

Every numeric claim in the brief is recomputed from the raw price bars in state
by :mod:`app.graph.recompute`, every quoted headline is matched verbatim against
the news actually retrieved, and the brief's structure is checked against the
run. Numbers match to the cent or nothing ships.

Outcomes:

* all checks pass → the gate
* any mismatch, first time → exactly one writer regeneration
* any mismatch, second time → ``HUMAN_REVIEW`` (flagged, never auto-delivered)
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.events import EventKind
from app.graph.context import current_context
from app.graph.recompute import recompute
from app.graph.state import RunState, is_partial, usable_tickers
from app.models.brief import Brief
from app.models.market import METRIC_UNITS, MetricUnit
from app.models.run import RunError, RunStatus

#: Absolute tolerance per unit. USD is "to the cent"; percentages are checked far
#: tighter than the 4-decimal rounding the tool applies on the way out.
TOLERANCES: dict[MetricUnit, float] = {
    "usd": 0.005,
    "percent": 0.001,
    "ratio": 0.001,
    "score": 0.000001,
}

_WHITESPACE = re.compile(r"\s+")

ClaimStatus = Literal["match", "mismatch", "unverifiable"]


def _normalise_quote(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip()).casefold()


class ClaimCheck(BaseModel):
    """One recomputed numeric claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    ticker: str
    metric: str
    unit: MetricUnit
    claimed: float
    recomputed: float | None
    delta: float | None
    tolerance: float
    status: ClaimStatus
    detail: str


class QuoteCheck(BaseModel):
    """One verbatim third-party quotation."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    field: str
    text: str
    status: Literal["match", "mismatch"]
    detail: str


class VerificationReport(BaseModel):
    """The full verification pass, rendered claim-by-claim in the UI."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    checked_claims: int = 0
    matched: int = 0
    mismatched: int = 0
    unverifiable: int = 0
    coverage: float = 0.0
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    quote_checks: list[QuoteCheck] = Field(default_factory=list)
    structural_issues: list[str] = Field(default_factory=list)
    structural_warnings: list[str] = Field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        out = [
            f"{c.claim_id} ({c.ticker}.{c.metric}): {c.detail}"
            for c in self.claim_checks
            if c.status != "match"
        ]
        out += [
            f"{q.ticker}.{q.field}: {q.detail}" for q in self.quote_checks if q.status != "match"
        ]
        out += self.structural_issues
        return out


def verify_brief(brief: Brief, state: RunState) -> VerificationReport:
    """Pure verification: recompute every claim from raw state.

    Importable and testable in isolation — no graph, no network, no model.
    """
    prices = state.get("prices", {})
    fundamentals = state.get("fundamentals", {})
    sentiment = state.get("sentiment", {})
    news = state.get("news", {})
    watchlist = set(state.get("tickers", []))

    structural: list[str] = []
    warnings: list[str] = []

    # ---------------------------------------------------------- structure ---
    expected_date = state.get("session_date")
    if expected_date and brief.generated_for != expected_date:
        structural.append(
            f"brief.generated_for '{brief.generated_for}' does not match the run's "
            f"session date '{expected_date}'"
        )
    if set(brief.watchlist) != watchlist:
        structural.append(
            f"brief.watchlist {sorted(brief.watchlist)} does not match the run watchlist "
            f"{sorted(watchlist)}"
        )

    defined = brief.claims_by_id()
    if len(defined) != len(brief.claims):
        structural.append("duplicate claim_id values in brief.claims")

    referenced = brief.referenced_claim_ids()
    missing_refs = sorted(referenced - defined.keys())
    if missing_refs:
        structural.append(f"narrative references undefined claims: {missing_refs}")
    orphans = sorted(defined.keys() - referenced)
    if orphans:
        warnings.append(f"claims defined but never cited: {orphans}")

    expected_rows = set(usable_tickers(state))
    row_tickers = [row.ticker for row in brief.snapshot]
    if len(row_tickers) != len(set(row_tickers)):
        structural.append("duplicate tickers in the snapshot table")
    unexpected = sorted(set(row_tickers) - watchlist)
    if unexpected:
        structural.append(f"snapshot contains tickers outside the watchlist: {unexpected}")
    dropped = sorted(expected_rows - set(row_tickers))
    if dropped:
        structural.append(f"snapshot omits tickers that have market data: {dropped}")

    for row in brief.snapshot:
        for claim_id in row.claim_ids():
            claim = defined.get(claim_id)
            if claim and claim.ticker != row.ticker:
                structural.append(
                    f"snapshot row {row.ticker} cites {claim_id}, which belongs to {claim.ticker}"
                )

    if brief.partial != is_partial(state):
        structural.append(
            f"brief.partial={brief.partial} but the run "
            f"{'has' if is_partial(state) else 'has no'} incomplete tickers"
        )

    # ------------------------------------------------------------- claims ---
    claim_checks: list[ClaimCheck] = []
    for claim in brief.claims:
        unit = METRIC_UNITS.get(claim.metric, claim.unit)
        tolerance = TOLERANCES[unit]
        if claim.ticker not in watchlist:
            claim_checks.append(
                ClaimCheck(
                    claim_id=claim.claim_id,
                    ticker=claim.ticker,
                    metric=claim.metric,
                    unit=unit,
                    claimed=claim.value,
                    recomputed=None,
                    delta=None,
                    tolerance=tolerance,
                    status="mismatch",
                    detail=f"ticker '{claim.ticker}' is not in the run watchlist",
                )
            )
            continue
        if claim.unit != unit:
            structural.append(
                f"{claim.claim_id} declares unit '{claim.unit}' but "
                f"{claim.metric} is measured in '{unit}'"
            )

        recomputed = recompute(
            claim.metric,
            history=prices.get(claim.ticker),
            fundamentals=fundamentals.get(claim.ticker),
            sentiment=sentiment.get(claim.ticker),
        )
        if recomputed is None:
            claim_checks.append(
                ClaimCheck(
                    claim_id=claim.claim_id,
                    ticker=claim.ticker,
                    metric=claim.metric,
                    unit=unit,
                    claimed=claim.value,
                    recomputed=None,
                    delta=None,
                    tolerance=tolerance,
                    status="unverifiable",
                    detail="raw state cannot support recomputing this figure",
                )
            )
            continue

        delta = abs(claim.value - recomputed)
        matched = delta <= tolerance
        claim_checks.append(
            ClaimCheck(
                claim_id=claim.claim_id,
                ticker=claim.ticker,
                metric=claim.metric,
                unit=unit,
                claimed=claim.value,
                recomputed=round(recomputed, 6),
                delta=round(delta, 8),
                tolerance=tolerance,
                status="match" if matched else "mismatch",
                detail=(
                    "recomputed from raw price bars"
                    if matched
                    else f"claimed {claim.value} vs recomputed {recomputed:.6f} (Δ {delta:.6f})"
                ),
            )
        )

    # ------------------------------------------------------------- quotes ---
    quote_checks: list[QuoteCheck] = []
    for ticker, field_name, text in brief.quoted_texts():
        feed = news.get(ticker)
        available = {_normalise_quote(item.title) for item in (feed.items if feed else [])}
        matched = _normalise_quote(text) in available
        quote_checks.append(
            QuoteCheck(
                ticker=ticker,
                field=field_name,
                text=text[:200],
                status="match" if matched else "mismatch",
                detail=(
                    "verbatim match against retrieved headlines"
                    if matched
                    else "quoted text does not appear in the headlines retrieved for this ticker"
                ),
            )
        )

    matched_count = sum(1 for c in claim_checks if c.status == "match")
    mismatched = sum(1 for c in claim_checks if c.status == "mismatch")
    unverifiable = sum(1 for c in claim_checks if c.status == "unverifiable")
    quote_failures = sum(1 for q in quote_checks if q.status == "mismatch")

    coverage = 1.0 if not claim_checks else matched_count / len(claim_checks)
    ok = mismatched == 0 and unverifiable == 0 and quote_failures == 0 and not structural

    return VerificationReport(
        ok=ok,
        checked_claims=len(claim_checks),
        matched=matched_count,
        mismatched=mismatched,
        unverifiable=unverifiable,
        coverage=round(coverage, 4),
        claim_checks=claim_checks,
        quote_checks=quote_checks,
        structural_issues=structural,
        structural_warnings=warnings,
    )


async def verify_node(state: RunState) -> dict[str, Any]:
    """Graph node: run verification and stream each claim to the UI."""
    ctx = current_context()
    brief = state.get("brief")

    if brief is None:
        await ctx.emit(EventKind.VERIFY_COMPLETED, "No brief to verify", {"ok": False})
        return {
            "verification": VerificationReport(
                ok=False, structural_issues=["writer produced no brief"]
            ).model_dump(),
            "errors": [RunError(stage="verify", message="writer produced no brief")],
            "status": RunStatus.HUMAN_REVIEW,
        }

    await ctx.emit(
        EventKind.VERIFY_STARTED,
        f"Recomputing {len(brief.claims)} numeric claims from raw state",
        {"claims": len(brief.claims)},
    )

    with ctx.tracer.step(
        "verify",
        run_id=ctx.run_id,
        input_data={"claims": len(brief.claims)},
    ) as span:
        report = verify_brief(brief, state)
        span.update(output={"ok": report.ok, "coverage": report.coverage})

    # Stream tick-by-tick so the verification screen animates green (or red).
    for check in report.claim_checks:
        await ctx.emit(
            EventKind.VERIFY_CLAIM,
            f"{check.ticker} · {check.metric}",
            check.model_dump(),
        )
    for quote in report.quote_checks:
        await ctx.emit(
            EventKind.VERIFY_CLAIM,
            f"{quote.ticker} · quoted headline",
            {**quote.model_dump(), "metric": "quoted_headline", "unit": "text"},
        )

    await ctx.emit(
        EventKind.VERIFY_COMPLETED,
        (
            f"All {report.matched} claims verified"
            if report.ok
            else f"{report.mismatched + report.unverifiable} claim(s) failed verification"
        ),
        report.model_dump(),
    )

    update: dict[str, Any] = {"verification": report.model_dump()}
    if report.ok:
        return update

    failures = report.failures
    update["errors"] = [
        RunError(stage="verify", message=detail, severity="error") for detail in failures[:10]
    ]
    if state.get("regenerations", 0) >= ctx.settings.max_regenerations:
        update["status"] = RunStatus.HUMAN_REVIEW
    return update


def route_after_verify(state: RunState) -> Literal["writer", "gate"]:
    """Exactly one regeneration on mismatch, then straight to the human gate.

    The cap lives in state (`max_regenerations`, default 1 per spec) so a
    mismatch can never ping-pong between writer and verifier.
    """
    report = state.get("verification") or {}
    if report.get("ok"):
        return "gate"
    if int(state.get("regenerations", 0)) < int(state.get("max_regenerations", 1)):
        return "writer"
    return "gate"
