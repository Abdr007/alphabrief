"""Race conditions: parallel agent writes must both survive.

Two levels of proof:

1. the reducers themselves are commutative merges, and
2. LangGraph actually applies them when two nodes write the same channels in the
   same superstep — run against a real compiled graph, not a mock.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.state import (
    RunState,
    append_errors,
    append_records,
    append_risk_events,
    append_unique_strings,
    merge_counters,
    merge_mapping,
    merge_spend,
)
from app.models.market import RiskEvent
from app.models.run import RunError, TokenSpend


class TestReducerUnits:
    def test_merge_mapping_keeps_both_sides(self) -> None:
        left = {"AAPL": 1, "MSFT": 2}
        right = {"NVDA": 3}
        assert merge_mapping(left, right) == {"AAPL": 1, "MSFT": 2, "NVDA": 3}

    def test_merge_mapping_handles_none(self) -> None:
        assert merge_mapping(None, {"A": 1}) == {"A": 1}
        assert merge_mapping({"A": 1}, None) == {"A": 1}
        assert merge_mapping(None, None) == {}

    def test_merge_counters_adds_per_key(self) -> None:
        assert merge_counters({"market:AAPL": 1}, {"market:AAPL": 1, "news:AAPL": 1}) == {
            "market:AAPL": 2,
            "news:AAPL": 1,
        }

    def test_append_errors_dedupes_identical_triples(self) -> None:
        error = RunError(stage="data_agent", ticker="AAPL", message="boom")
        duplicate = RunError(stage="data_agent", ticker="AAPL", message="boom")
        other = RunError(stage="news_agent", ticker="AAPL", message="boom")
        merged = append_errors([error], [duplicate, other])
        assert len(merged) == 2

    def test_append_records_is_bounded(self) -> None:
        many: list[dict[str, Any]] = [{"i": i} for i in range(5000)]
        merged = append_records([], many)
        assert len(merged) <= 2000
        # The most recent rows are the ones kept.
        assert merged[-1] == {"i": 4999}

    def test_append_risk_events_dedupes(self) -> None:
        event = RiskEvent(ticker="AAPL", category="legal", headline="Apple sued")
        merged = append_risk_events([event], [event])
        assert len(merged) == 1

    def test_append_unique_strings_preserves_order(self) -> None:
        assert append_unique_strings(["a"], ["b", "a", "c"]) == ["a", "b", "c"]

    def test_merge_spend_sums_fields(self) -> None:
        left = TokenSpend(input_tokens=10, output_tokens=5, cost_usd=0.001, calls=1)
        right = TokenSpend(input_tokens=3, output_tokens=2, cost_usd=0.002, calls=1)
        merged = merge_spend(left, right)
        assert merged.input_tokens == 13
        assert merged.output_tokens == 7
        assert merged.calls == 2
        assert merged.cost_usd == 0.003

    def test_reducers_are_commutative_for_disjoint_writes(self) -> None:
        a = {"AAPL": 1}
        b = {"MSFT": 2}
        assert merge_mapping(a, b) == merge_mapping(b, a)


class TestParallelSupersteps:
    """The mandated proof: concurrent writes from two nodes both survive."""

    async def test_both_agents_writes_survive_one_superstep(self) -> None:
        started: list[str] = []

        async def data_like(state: RunState) -> dict[str, Any]:
            started.append("data")
            # Yield control so the two nodes genuinely interleave.
            await asyncio.sleep(0.01)
            return {
                "metrics": {"AAPL": "market"},
                "errors": [RunError(stage="data_agent", ticker="AAPL", message="d")],
                "attempts": {"market:AAPL": 1},
                "tool_calls": [{"tool": "get_price_history"}],
                "agents_completed": ["data_agent"],
                "token_spend": TokenSpend(input_tokens=10, calls=1),
                "iterations": 0,
            }

        async def news_like(state: RunState) -> dict[str, Any]:
            started.append("news")
            await asyncio.sleep(0.01)
            return {
                "sentiment": {"AAPL": "news"},
                "errors": [RunError(stage="news_agent", ticker="AAPL", message="n")],
                "attempts": {"news:AAPL": 1},
                "tool_calls": [{"tool": "fetch_rss_news"}],
                "agents_completed": ["news_agent"],
                "token_spend": TokenSpend(input_tokens=7, calls=1),
                "iterations": 0,
            }

        async def fan_out(state: RunState) -> dict[str, Any]:
            return {"iterations": 1}

        graph: StateGraph[RunState, None, RunState, RunState] = StateGraph(RunState)
        graph.add_node("fan_out", fan_out)
        graph.add_node("data", data_like)
        graph.add_node("news", news_like)
        graph.add_edge(START, "fan_out")
        graph.add_conditional_edges("fan_out", lambda _s: ["data", "news"], ["data", "news"])
        graph.add_edge("data", END)
        graph.add_edge("news", END)
        compiled = graph.compile()

        result = await compiled.ainvoke(
            RunState(
                tickers=["AAPL"],
                metrics={},
                sentiment={},
                errors=[],
                attempts={},
                tool_calls=[],
                agents_completed=[],
                iterations=0,
                token_spend=TokenSpend(),
            )
        )

        assert set(started) == {"data", "news"}
        # Neither side clobbered the other on ANY shared channel.
        assert result["metrics"] == {"AAPL": "market"}
        assert result["sentiment"] == {"AAPL": "news"}
        assert sorted(result["agents_completed"]) == ["data_agent", "news_agent"]
        assert result["attempts"] == {"market:AAPL": 1, "news:AAPL": 1}
        assert len(result["errors"]) == 2
        assert len(result["tool_calls"]) == 2
        assert result["token_spend"].calls == 2
        assert result["token_spend"].input_tokens == 17
        assert result["iterations"] == 1
