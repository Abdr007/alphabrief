"""The MCP tool layer: discoverability, contracts, caching, rate limiting.

Exercised over the real MCP protocol against live providers.
"""

from __future__ import annotations

import time

import pytest

from app.core.events import EventBus
from app.core.settings import Settings
from app.mcp_server.providers import (
    MAX_HISTORY_DAYS,
    MAX_NEWS_LIMIT,
    ProviderContext,
    fetch_price_history,
    normalise_ticker,
)
from app.mcp_server.registry import SERVER_INSTRUCTIONS, TOOL_NAMES
from tests.conftest import connected_context

LIVE_TICKER = "AAPL"


class TestDiscoverability:
    async def test_all_tools_are_advertised_with_documentation(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            specs = await ctx.mcp.list_tool_specs()

        assert {spec["name"] for spec in specs} == TOOL_NAMES
        for spec in specs:
            # Agents behave as well as their tool docs: every tool must have real prose.
            assert len(spec["description"]) > 120, spec["name"]
            assert spec["input_schema"].get("type") == "object"
            assert spec["input_schema"].get("properties")

    def test_server_instructions_forbid_model_arithmetic(self) -> None:
        assert "never estimate" in SERVER_INSTRUCTIONS.lower()

    async def test_tool_schemas_declare_their_required_arguments(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            specs = {spec["name"]: spec for spec in await ctx.mcp.list_tool_specs()}

        assert "ticker" in specs["get_price_history"]["input_schema"]["properties"]
        assert "days" in specs["get_price_history"]["input_schema"]["properties"]
        assert "bars" in specs["compute_metrics"]["input_schema"]["properties"]
        assert "limit" in specs["fetch_rss_news"]["input_schema"]["properties"]


class TestLiveToolCalls:
    async def test_full_market_data_round_trip(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            history = await ctx.mcp.get_price_history(LIVE_TICKER, 120)
            fundamentals = await ctx.mcp.get_fundamentals(LIVE_TICKER)
            metrics = await ctx.mcp.compute_metrics(
                LIVE_TICKER, list(history.bars), fundamentals.pe_ratio
            )

        if not history.ok:
            pytest.skip(f"provider unavailable: {history.error}")

        assert len(history.bars) > 20
        assert metrics.ok
        assert metrics.last_close is not None and metrics.last_close > 0
        assert metrics.window_start <= metrics.window_end  # type: ignore[operator]
        assert metrics.sample_size == len(history.bars)
        # Bars arrive oldest → newest.
        assert [bar.date for bar in history.bars] == sorted(bar.date for bar in history.bars)

    async def test_every_call_is_recorded_with_timing(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            await ctx.mcp.get_price_history(LIVE_TICKER, 30)
            records = list(ctx.mcp.records)

        assert records
        record = records[-1]
        assert record.tool == "get_price_history"
        assert record.duration_ms >= 0
        assert "ticker" in record.arguments
        assert isinstance(record.to_dict()["duration_ms"], float)

    async def test_collect_scopes_records_to_one_block(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            await ctx.mcp.get_price_history(LIVE_TICKER, 30)
            with ctx.mcp.collect() as sink:
                await ctx.mcp.get_fundamentals(LIVE_TICKER)
            assert len(sink) == 1
            assert sink[0].tool == "get_fundamentals"

    async def test_emitter_streams_calls_live(self, settings: Settings) -> None:
        seen: list[str] = []

        async with connected_context(settings, EventBus()) as ctx:
            ctx.mcp.emitter = lambda record: _record(seen, record.tool)
            await ctx.mcp.get_price_history(LIVE_TICKER, 30)

        assert seen == ["get_price_history"]


async def _record(sink: list[str], value: str) -> None:
    sink.append(value)


class TestCachingAndRateLimiting:
    async def test_repeat_requests_hit_the_per_run_cache(self) -> None:
        ctx = ProviderContext(min_interval_seconds=0.0)
        first = await fetch_price_history(ctx, LIVE_TICKER, 30)
        if not first.ok:
            pytest.skip(f"provider unavailable: {first.error}")

        started = time.perf_counter()
        second = await fetch_price_history(ctx, LIVE_TICKER, 30)
        elapsed = time.perf_counter() - started

        assert second is first
        assert elapsed < 0.05
        assert ctx.stats()["cache_hits"] >= 1

    async def test_throttle_enforces_a_minimum_gap(self) -> None:
        ctx = ProviderContext(min_interval_seconds=0.05)
        await ctx.throttle()
        started = time.perf_counter()
        await ctx.throttle()
        assert time.perf_counter() - started >= 0.04


class TestInputBounds:
    def test_ticker_normalisation_is_strict(self) -> None:
        assert normalise_ticker(" aapl ") == "AAPL"
        assert normalise_ticker("brk.b") == "BRK.B"
        with pytest.raises(ValueError):
            normalise_ticker("AAPL/../etc")

    async def test_absurd_windows_are_clamped_not_rejected(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            history = await ctx.mcp.get_price_history(LIVE_TICKER, 10_000_000)
        assert history.days_requested <= MAX_HISTORY_DAYS

    async def test_absurd_news_limits_are_clamped(self, settings: Settings) -> None:
        async with connected_context(settings, EventBus()) as ctx:
            feed = await ctx.mcp.fetch_rss_news(LIVE_TICKER, 10_000)
        assert len(feed.items) <= MAX_NEWS_LIMIT
