"""Numeric integrity: every claim is recomputed, and mismatches never ship.

These run against a state built from live market data, so a passing verification
means the figures genuinely reconcile against real price bars.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.graph.compose import compose_brief
from app.graph.recompute import recompute
from app.graph.state import RunState
from app.graph.verify import TOLERANCES, route_after_verify, verify_brief
from app.mcp_server import metrics as metric_math
from app.mcp_server.registry import compute_metrics_from_bars
from app.models.brief import Brief
from app.models.market import PriceHistory


def _brief_from(state: RunState) -> Brief:
    return compose_brief(
        session_date=str(state["session_date"]),
        tickers=list(state["tickers"]),
        metrics=state["metrics"],
        fundamentals=state["fundamentals"],
        sentiment=state["sentiment"],
        news=state["news"],
        risk_events=[],
        data_gaps=[],
    )


class TestDualPathAgreement:
    """The tool and the verifier compute the same figures via different code."""

    def test_volatility_agrees(self, live_history: PriceHistory) -> None:
        closes = [bar.close for bar in live_history.bars]
        tool = metric_math.annualised_volatility_pct(closes)
        verifier = recompute(
            "volatility_annualised_pct", history=live_history, fundamentals=None, sentiment=None
        )
        assert tool is not None and verifier is not None
        assert tool == pytest.approx(verifier, abs=1e-9)

    def test_max_drawdown_agrees(self, live_history: PriceHistory) -> None:
        closes = [bar.close for bar in live_history.bars]
        tool = metric_math.max_drawdown_pct(closes)
        verifier = recompute(
            "max_drawdown_pct", history=live_history, fundamentals=None, sentiment=None
        )
        assert tool is not None and verifier is not None
        assert tool == pytest.approx(verifier, abs=1e-9)

    def test_return_30d_agrees(self, live_history: PriceHistory) -> None:
        dates = [bar.date for bar in live_history.bars]
        closes = [bar.close for bar in live_history.bars]
        tool, _baseline = metric_math.return_over_window_pct(dates, closes)
        verifier = recompute(
            "return_30d_pct", history=live_history, fundamentals=None, sentiment=None
        )
        assert tool is not None and verifier is not None
        assert tool == pytest.approx(verifier, abs=1e-9)

    def test_change_1d_agrees(self, live_history: PriceHistory) -> None:
        closes = [bar.close for bar in live_history.bars]
        tool = metric_math.change_1d_pct(closes)
        verifier = recompute(
            "change_1d_pct", history=live_history, fundamentals=None, sentiment=None
        )
        assert tool is not None and verifier is not None
        assert tool == pytest.approx(verifier, abs=1e-9)

    def test_drawdown_is_never_positive(self, live_history: PriceHistory) -> None:
        value = recompute(
            "max_drawdown_pct", history=live_history, fundamentals=None, sentiment=None
        )
        assert value is not None
        assert value <= 0.0


class TestCleanVerification:
    def test_live_brief_verifies_fully(self, live_state: RunState) -> None:
        report = verify_brief(_brief_from(live_state), live_state)
        assert report.ok, report.failures
        assert report.checked_claims > 0
        assert report.mismatched == 0
        assert report.unverifiable == 0
        assert report.coverage == 1.0

    def test_every_claim_is_checked(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        report = verify_brief(brief, live_state)
        assert report.checked_claims == len(brief.claims)

    def test_quoted_headlines_are_matched_verbatim(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        report = verify_brief(brief, live_state)
        if brief.quoted_texts():
            assert report.quote_checks
            assert all(q.status == "match" for q in report.quote_checks)


class TestMismatchDetection:
    def test_corrupted_claim_is_caught(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        target = brief.claims[0]
        corrupted = brief.model_copy(
            update={
                "claims": [
                    target.model_copy(update={"value": target.value + 12.5}),
                    *brief.claims[1:],
                ]
            }
        )
        report = verify_brief(corrupted, live_state)
        assert not report.ok
        assert report.mismatched == 1
        bad = next(c for c in report.claim_checks if c.status == "mismatch")
        assert bad.claim_id == target.claim_id
        assert bad.delta is not None and bad.delta > bad.tolerance

    def test_a_sub_tolerance_nudge_still_passes(self, live_state: RunState) -> None:
        """The tolerance is real, not infinite: a cent-level rounding is fine."""
        brief = _brief_from(live_state)
        target = next(c for c in brief.claims if c.unit == "usd")
        nudged = brief.model_copy(
            update={
                "claims": [
                    c.model_copy(update={"value": c.value + TOLERANCES["usd"] / 2})
                    if c.claim_id == target.claim_id
                    else c
                    for c in brief.claims
                ]
            }
        )
        report = verify_brief(nudged, live_state)
        assert report.ok, report.failures

    def test_fabricated_quote_is_caught(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        if not brief.news_and_sentiment:
            pytest.skip("no news block to corrupt")
        blocks = [b.model_copy() for b in brief.news_and_sentiment]
        blocks[0] = blocks[0].model_copy(
            update={"top_headline": "A headline that was never retrieved anywhere"}
        )
        corrupted = brief.model_copy(update={"news_and_sentiment": blocks})
        report = verify_brief(corrupted, live_state)
        assert not report.ok
        assert any(q.status == "mismatch" for q in report.quote_checks)

    def test_wrong_session_date_is_structural_failure(self, live_state: RunState) -> None:
        brief = _brief_from(live_state).model_copy(update={"generated_for": "1999-01-01"})
        report = verify_brief(brief, live_state)
        assert not report.ok
        assert any("session date" in issue for issue in report.structural_issues)

    def test_claim_for_ticker_outside_watchlist_is_caught(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        rogue = brief.claims[0].model_copy(update={"ticker": "NOTINLIST"})
        corrupted = brief.model_copy(update={"claims": [*brief.claims, rogue]})
        report = verify_brief(corrupted, live_state)
        assert not report.ok
        assert any("not in the run watchlist" in c.detail for c in report.claim_checks)

    def test_dangling_claim_reference_is_structural_failure(self, live_state: RunState) -> None:
        brief = _brief_from(live_state)
        corrupted = brief.model_copy(update={"executive_summary": "Everything is fine {{c999}}."})
        report = verify_brief(corrupted, live_state)
        assert not report.ok
        assert any("undefined claims" in issue for issue in report.structural_issues)

    def test_unverifiable_claim_blocks_delivery(self, live_state: RunState) -> None:
        """A claim we cannot recompute is treated as a failure, never waved through."""
        brief = _brief_from(live_state)
        stripped: dict[str, Any] = dict(live_state)
        stripped["prices"] = {}
        report = verify_brief(brief, stripped)  # type: ignore[arg-type]
        assert not report.ok
        assert report.unverifiable > 0


class TestRegenerationRouting:
    def test_clean_verification_goes_to_the_gate(self) -> None:
        state = RunState(verification={"ok": True}, regenerations=0, max_regenerations=1)
        assert route_after_verify(state) == "gate"

    def test_first_mismatch_triggers_one_regeneration(self) -> None:
        state = RunState(verification={"ok": False}, regenerations=0, max_regenerations=1)
        assert route_after_verify(state) == "writer"

    def test_second_mismatch_goes_to_human_review(self) -> None:
        state = RunState(verification={"ok": False}, regenerations=1, max_regenerations=1)
        assert route_after_verify(state) == "gate"


class TestInsufficientData:
    def test_single_bar_yields_an_error_not_a_crash(self, live_history: PriceHistory) -> None:
        one_bar = live_history.model_copy(update={"bars": live_history.bars[:1]})
        metrics = compute_metrics_from_bars(one_bar.ticker, list(one_bar.bars), None)
        assert not metrics.ok
        assert metrics.error is not None

    def test_no_bars_yields_an_error_not_a_crash(self, live_history: PriceHistory) -> None:
        empty = live_history.model_copy(update={"bars": []})
        metrics = compute_metrics_from_bars(empty.ticker, [], None)
        assert not metrics.ok
        assert recompute("last_close", history=empty, fundamentals=None, sentiment=None) is None
