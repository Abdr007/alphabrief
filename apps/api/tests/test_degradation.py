"""Graceful degradation: fake ticker, dead network, empty RSS.

All three failures are induced for real — an unknown symbol really is requested
from the provider, the network really is unreachable (an unroutable proxy), and
the empty feed really comes back empty. Nothing here is mocked, because the
behaviour under test is precisely what happens when the outside world misbehaves.

In every case the requirement is identical: per-item errors are recorded, the run
continues, and the brief is explicitly marked partial. Never a crash.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.events import EventBus
from app.core.settings import Settings
from app.graph.compose import compose_brief
from app.graph.verify import verify_brief
from app.mcp_server.providers import (
    ProviderContext,
    call_provider,
    fetch_fundamentals,
    fetch_price_history,
    fetch_rss_news,
    normalise_ticker,
)
from app.mcp_server.registry import compute_metrics_from_bars
from tests.conftest import connected_context

#: A symbol no exchange lists. Verified against the live provider.
UNKNOWN_TICKER = "ZZZZQQQQ"
#: Loopback port with nothing listening: a real, immediate connection failure.
DEAD_PROXY = "http://127.0.0.1:9"


class TestFakeTicker:
    async def test_price_history_degrades_with_an_error(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            history = await ctx.mcp.get_price_history(UNKNOWN_TICKER, 60)
        assert not history.ok
        assert history.error is not None
        assert history.bars == []

    async def test_fundamentals_degrade_with_an_error(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            fundamentals = await ctx.mcp.get_fundamentals(UNKNOWN_TICKER)
        assert not fundamentals.ok
        assert fundamentals.error is not None

    async def test_metrics_degrade_rather_than_raise(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            history = await ctx.mcp.get_price_history(UNKNOWN_TICKER, 60)
            metrics = await ctx.mcp.compute_metrics(UNKNOWN_TICKER, list(history.bars), None)
        assert not metrics.ok
        assert "insufficient price history" in (metrics.error or "")

    def test_malformed_symbols_are_rejected_before_any_request(self) -> None:
        for bad in ("", "   ", "A" * 40, "AAPL; DROP TABLE runs", "../../etc/passwd", "<script>"):
            with pytest.raises(ValueError):
                normalise_ticker(bad)

    def test_partial_brief_is_marked_and_still_verifies(
        self, live_state: dict[str, Any], live_history: object
    ) -> None:
        """A run with one good and one dead ticker produces a *flagged* partial brief."""
        state = dict(live_state)
        good = state["tickers"][0]
        state["tickers"] = [good, UNKNOWN_TICKER]

        brief = compose_brief(
            session_date=str(state["session_date"]),
            tickers=list(state["tickers"]),
            metrics=state["metrics"],
            fundamentals=state["fundamentals"],
            sentiment=state["sentiment"],
            news=state["news"],
            risk_events=[],
            data_gaps=[f"{UNKNOWN_TICKER}: no price data returned"],
        )

        assert brief.partial is True
        assert [row.ticker for row in brief.snapshot] == [good]
        assert brief.data_gaps

        report = verify_brief(brief, state)  # type: ignore[arg-type]
        assert report.ok, report.failures


class TestDeadNetwork:
    """Real connection failure via an unroutable proxy — no mocking."""

    async def test_price_history_survives_a_dead_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, DEAD_PROXY)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        ctx = ProviderContext(min_interval_seconds=0.0, max_attempts=1)
        history = await fetch_price_history(ctx, "AAPL", 30)

        assert not history.ok
        assert history.error is not None
        assert history.bars == []

    async def test_news_survives_a_dead_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, DEAD_PROXY)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        ctx = ProviderContext(min_interval_seconds=0.0, max_attempts=1)
        feed = await fetch_rss_news(ctx, "AAPL", 5)

        assert feed.items == []
        assert feed.error is not None
        assert "unavailable" in feed.error

    async def test_fundamentals_survive_a_dead_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, DEAD_PROXY)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        ctx = ProviderContext(min_interval_seconds=0.0, max_attempts=1)
        fundamentals = await fetch_fundamentals(ctx, "AAPL")

        assert not fundamentals.ok
        assert fundamentals.error is not None

    async def test_a_totally_dead_run_still_produces_a_marked_brief(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worst case: nothing resolves. The brief must still be a valid object."""
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, DEAD_PROXY)

        ctx = ProviderContext(min_interval_seconds=0.0, max_attempts=1)
        history = await fetch_price_history(ctx, "AAPL", 30)
        metrics = compute_metrics_from_bars("AAPL", list(history.bars), None)

        brief = compose_brief(
            session_date="2026-01-02",
            tickers=["AAPL"],
            metrics={"AAPL": metrics},
            fundamentals={},
            sentiment={},
            news={},
            risk_events=[],
            data_gaps=["AAPL: price provider unavailable"],
        )
        assert brief.partial is True
        assert brief.snapshot == []
        assert brief.claims == []
        assert "no ticker" in brief.executive_summary.lower()


class TestEmptyRss:
    async def test_a_feed_with_no_items_is_not_an_error(self, settings: Settings) -> None:
        """No coverage is a legitimate outcome, distinct from a failed fetch."""
        async with connected_context(settings, EventBus()) as ctx:
            feed = await ctx.mcp.fetch_rss_news(UNKNOWN_TICKER, 5)

        assert feed.error is None
        assert feed.items == []
        assert feed.is_empty is True

    def test_empty_headlines_score_neutral_with_stated_reasoning(self) -> None:
        from app.core.claude import score_headlines

        score, reasoning = score_headlines([])
        assert score == 0.0
        assert "no headlines" in reasoning.lower()

    def test_brief_states_the_absence_rather_than_inventing_sentiment(
        self, live_state: dict[str, Any]
    ) -> None:
        from app.models.market import NewsFeed, Sentiment

        state = dict(live_state)
        ticker = state["tickers"][0]
        state["news"] = {ticker: NewsFeed(ticker=ticker, items=[], feeds_queried=["yahoo"])}
        state["sentiment"] = {
            ticker: Sentiment(
                ticker=ticker,
                score=0.0,
                reasoning="No headlines were retrieved, so sentiment defaults to neutral.",
                headline_count=0,
            )
        }

        brief = compose_brief(
            session_date=str(state["session_date"]),
            tickers=list(state["tickers"]),
            metrics=state["metrics"],
            fundamentals=state["fundamentals"],
            sentiment=state["sentiment"],
            news=state["news"],
            risk_events=[],
            data_gaps=[],
        )
        block = next(b for b in brief.news_and_sentiment if b.ticker == ticker)
        assert block.top_headline is None
        assert "no recent headlines" in block.summary.lower()

        report = verify_brief(brief, state)  # type: ignore[arg-type]
        assert report.ok, report.failures


class TestTransientProviderFailuresAreRetried:
    """A cold session's first call is answered with a rate-limit error.

    Found on the deployed Space, not locally: Yahoo rate-limited the *first*
    outbound call from the container's shared egress IP and then served every
    call after it, so the first ticker of every hosted run came back empty. The
    brief reported it honestly as partial coverage — which is the degradation
    path working — but the run was still missing half its data every time.
    """

    async def test_a_call_that_fails_once_then_succeeds_returns_the_result(self) -> None:
        ctx = ProviderContext(min_interval_seconds=0.0, retry_backoff_seconds=0.0)
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("YFRateLimitError")
            return "bars"

        result = await call_provider(ctx, flaky, label="flaky")

        assert result == "bars"
        assert attempts["n"] == 2

    async def test_it_gives_up_after_the_configured_attempts(self) -> None:
        """Bounded: a genuinely dead provider must not stall the run indefinitely."""
        ctx = ProviderContext(min_interval_seconds=0.0, retry_backoff_seconds=0.0, max_attempts=3)
        attempts = {"n": 0}

        def always_fails() -> str:
            attempts["n"] += 1
            raise RuntimeError("down")

        with pytest.raises(RuntimeError, match="down"):
            await call_provider(ctx, always_fails, label="dead")

        assert attempts["n"] == 3

    async def test_a_single_attempt_never_retries(self) -> None:
        ctx = ProviderContext(min_interval_seconds=0.0, retry_backoff_seconds=0.0, max_attempts=1)
        attempts = {"n": 0}

        def always_fails() -> str:
            attempts["n"] += 1
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await call_provider(ctx, always_fails, label="dead")

        assert attempts["n"] == 1

    async def test_the_original_error_survives_the_retries(self) -> None:
        """The tool reports `type(exc).__name__`, so the last exception must be the real one."""
        ctx = ProviderContext(min_interval_seconds=0.0, retry_backoff_seconds=0.0, max_attempts=2)

        def always_fails() -> str:
            raise TimeoutError("upstream timed out")

        with pytest.raises(TimeoutError, match="upstream timed out"):
            await call_provider(ctx, always_fails, label="slow")
