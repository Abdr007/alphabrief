"""Shared fixtures.

Test data policy: metrics, verification and degradation tests run against **real
market data pulled live once per session**, not against invented fixtures. Only
*structurally degenerate* inputs (zero bars, one bar) are constructed by hand,
because those shapes cannot be obtained from a working provider — and they are
exactly the shapes the degradation paths must survive.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

# Configured before any app import so the settings singleton picks these up.
os.environ.setdefault("LLM_ENGINE", "deterministic")
os.environ.setdefault("MCP_TRANSPORT", "inmemory")
os.environ.setdefault("APPROVAL_TOKEN", "test-approval-token")
os.environ.setdefault("PROVIDER_MIN_INTERVAL_SECONDS", "0.05")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "DATABASE_URL", f"sqlite:///{Path(tempfile.mkdtemp(prefix='alphabrief-test-')) / 'app.db'}"
)

# The suite must never send real email. `Settings` reads `../../.env`, so an
# operator's working SMTP credentials would otherwise be picked up and the
# integration tests — which approve briefs — would mail a real inbox on every
# run. Assigned rather than setdefault: this has to beat the dotenv file, and
# environment variables take precedence over it.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USERNAME"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["SMTP_FROM"] = ""
os.environ["SMTP_TO"] = ""

from app.core.budget import BudgetGuard
from app.core.claude import DeterministicEngine
from app.core.events import EventBus
from app.core.settings import Settings
from app.core.tracing import Tracer
from app.graph.context import RunContext
from app.mcp_server.client import McpToolClient
from app.mcp_server.providers import (
    ProviderContext,
    fetch_fundamentals,
    fetch_price_history,
    fetch_rss_news,
)
from app.models.market import Fundamentals, NewsFeed, PriceHistory

LIVE_TICKER = "AAPL"
LIVE_DAYS = 120


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Isolated settings: temp SQLite database, in-memory MCP, offline engine."""
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    return Settings(
        database_url=f"sqlite:///{db_path}",
        llm_engine="deterministic",
        mcp_transport="inmemory",
        approval_token="test-approval-token",
        provider_min_interval_seconds=0.05,
        price_history_days=LIVE_DAYS,
        news_limit=5,
    )


@pytest.fixture(scope="session")
def live_market_data() -> dict[str, Any]:
    """Real price history, fundamentals and headlines, fetched once per session.

    Skips (rather than fails) when the machine has no network, so the suite is
    still usable offline — the network-dependent guarantees are then explicitly
    unverified rather than silently faked.
    """

    async def _fetch() -> dict[str, Any]:
        ctx = ProviderContext(min_interval_seconds=0.05)
        history = await fetch_price_history(ctx, LIVE_TICKER, LIVE_DAYS)
        fundamentals = await fetch_fundamentals(ctx, LIVE_TICKER)
        news = await fetch_rss_news(ctx, LIVE_TICKER, 5)
        return {"history": history, "fundamentals": fundamentals, "news": news}

    try:
        data = asyncio.run(_fetch())
    except Exception as exc:  # noqa: BLE001 - offline is a skip, not a failure
        pytest.skip(f"live market data unavailable: {exc}")

    history: PriceHistory = data["history"]
    if not history.ok:
        pytest.skip(f"live market data unavailable: {history.error}")
    return data


@pytest.fixture(scope="session")
def live_history(live_market_data: dict[str, Any]) -> PriceHistory:
    history: PriceHistory = live_market_data["history"]
    return history


@pytest.fixture(scope="session")
def live_fundamentals(live_market_data: dict[str, Any]) -> Fundamentals:
    fundamentals: Fundamentals = live_market_data["fundamentals"]
    return fundamentals


@pytest.fixture(scope="session")
def live_news(live_market_data: dict[str, Any]) -> NewsFeed:
    feed: NewsFeed = live_market_data["news"]
    return feed


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus(max_events_per_run=500)


@pytest.fixture
def mcp_factory(settings: Settings) -> Callable[[], McpToolClient]:
    """Build an MCP client the test opens itself.

    The session is deliberately **not** opened by a fixture: the MCP SDK uses
    anyio task groups, and an async fixture that holds one open across `yield`
    enters and exits its cancel scope in different tasks under pytest-asyncio.
    Tests open the session inside their own task with `async with`.
    """

    def _make() -> McpToolClient:
        return McpToolClient(settings=settings)

    return _make


def build_run_context(
    settings: Settings,
    bus: EventBus,
    mcp: McpToolClient,
    *,
    run_id: str = "run_test",
) -> RunContext:
    """A RunContext wired to the offline engine."""
    return RunContext(
        run_id=run_id,
        settings=settings,
        engine=DeterministicEngine(settings),
        mcp=mcp,
        bus=bus,
        tracer=Tracer(settings),
        budget=BudgetGuard(settings),
    )


@asynccontextmanager
async def connected_context(
    settings: Settings, bus: EventBus, *, run_id: str = "run_test"
) -> AsyncIterator[RunContext]:
    """A RunContext with a live in-memory MCP session, opened in the caller's task."""
    async with McpToolClient(settings=settings) as client:
        yield build_run_context(settings, bus, client, run_id=run_id)


@pytest.fixture
def run_context(settings: Settings, event_bus: EventBus) -> RunContext:
    """A RunContext with an unconnected MCP client.

    Sufficient for every node that does not call a tool (supervisor, writer,
    verifier, gate). Tests that need real tool calls use `connected_context`.
    """
    return build_run_context(settings, event_bus, McpToolClient(settings=settings))


@pytest.fixture
def temp_database_url() -> Iterator[str]:
    directory = Path(tempfile.mkdtemp())
    yield f"sqlite:///{directory / 'repo.db'}"


@pytest.fixture
def live_state(
    live_history: PriceHistory, live_fundamentals: Fundamentals, live_news: NewsFeed
) -> Any:
    """A RunState populated from live market data via the real pipeline functions.

    Metrics come from the same `compute_metrics_from_bars` the MCP tool calls, and
    sentiment from the same lexicon scorer the offline engine uses — so the state
    under test is byte-identical to what a real run produces.
    """
    from app.core.claude import score_headlines
    from app.graph.state import initial_state
    from app.mcp_server.registry import compute_metrics_from_bars
    from app.models.market import Sentiment

    ticker = live_history.ticker
    metrics = compute_metrics_from_bars(ticker, list(live_history.bars), live_fundamentals.pe_ratio)
    score, reasoning = score_headlines([item.title for item in live_news.items])

    state = initial_state(
        run_id="run_live",
        watchlist_key=ticker,
        tickers=[ticker],
        session_date="2026-01-02",
    )
    state["prices"] = {ticker: live_history}
    state["fundamentals"] = {ticker: live_fundamentals}
    state["metrics"] = {ticker: metrics}
    state["news"] = {ticker: live_news}
    state["sentiment"] = {
        ticker: Sentiment(
            ticker=ticker,
            score=score,
            reasoning=reasoning,
            headline_count=len(live_news.items),
        )
    }
    return state
