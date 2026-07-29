"""Loop safety: the hard iteration cap and the token/spend budget guard.

Both brakes are proved to actually stop a run, not merely to exist.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.budget import BudgetExceededError, BudgetGuard, Usage
from app.core.settings import Settings
from app.graph.context import RunContext, use_context
from app.graph.state import RunState, initial_state
from app.graph.supervisor import route_from_supervisor, supervisor_node
from app.models.run import RunStatus, TokenSpend


def _state(settings: Settings, iterations: int = 0) -> RunState:
    state = initial_state(
        run_id="run_loop",
        watchlist_key="AAPL",
        tickers=["AAPL"],
        session_date="2026-01-02",
        days=settings.price_history_days,
        news_limit=settings.news_limit,
    )
    state["iterations"] = iterations
    return state


class TestIterationCap:
    async def test_supervisor_aborts_at_the_cap(
        self, run_context: RunContext, settings: Settings
    ) -> None:
        state = _state(settings, iterations=settings.max_iterations)
        with use_context(run_context):
            update = await supervisor_node(state)

        assert update["status"] == RunStatus.ITERATION_ABORT
        assert "iteration cap" in (update["abort_reason"] or "")
        assert update["plan"]["dispatch"] == []

    def test_routing_ends_the_graph_on_abort(self) -> None:
        aborted = RunState(status=RunStatus.ITERATION_ABORT, plan={"dispatch": ["data_agent"]})
        assert route_from_supervisor(aborted) == END

    async def test_forced_loop_terminates(
        self, run_context: RunContext, settings: Settings
    ) -> None:
        """A worker that never completes its ticker must still terminate the run.

        This is the mandated forced-loop scenario: the workers are wired to return
        nothing at all, so the watchlist can never become complete. The run must
        stop at the iteration cap rather than spin forever.
        """

        async def never_completes(state: RunState) -> dict[str, Any]:
            # Records an attempt but never produces metrics or sentiment.
            return {"agents_completed": ["stub"]}

        graph: StateGraph[RunState, RunContext, RunState, RunState] = StateGraph(
            RunState, context_schema=RunContext
        )
        graph.add_node("supervisor", supervisor_node)
        graph.add_node("data_agent", never_completes)
        graph.add_node("news_agent", never_completes)
        graph.add_node("writer", never_completes)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            route_from_supervisor,
            ["data_agent", "news_agent", "writer", END],
        )
        graph.add_edge("data_agent", "supervisor")
        graph.add_edge("news_agent", "supervisor")
        graph.add_edge("writer", END)
        compiled = graph.compile()

        result = await compiled.ainvoke(
            _state(settings),
            {"recursion_limit": 200},
            context=run_context,
        )

        # It terminated, and it terminated for the documented reason.
        assert result["iterations"] <= settings.max_iterations + 1
        assert result["status"] == RunStatus.ITERATION_ABORT
        assert "iteration cap" in result["abort_reason"]


class TestBudgetGuard:
    def test_charge_accumulates_and_trips(self) -> None:
        guard = BudgetGuard(Settings(token_budget_usd=0.01, token_budget_tokens=1_000_000))
        snapshot = guard.charge("claude-haiku-4-5", Usage(input_tokens=1000, output_tokens=100))
        assert snapshot.calls == 1
        assert snapshot.spent_tokens == 1100

        with pytest.raises(BudgetExceededError):
            for _ in range(200):
                guard.charge("claude-sonnet-4-6", Usage(input_tokens=100_000, output_tokens=10_000))

    def test_token_ceiling_trips_independently_of_usd(self) -> None:
        guard = BudgetGuard(Settings(token_budget_usd=1000.0, token_budget_tokens=500))
        with pytest.raises(BudgetExceededError) as exc:
            guard.charge("claude-haiku-4-5", Usage(input_tokens=600))
        assert exc.value.limit_tokens == 500

    def test_preflight_refuses_a_call_that_cannot_fit(self) -> None:
        guard = BudgetGuard(Settings(token_budget_usd=0.0001, token_budget_tokens=1_000_000))
        with pytest.raises(BudgetExceededError):
            guard.ensure_headroom("claude-sonnet-4-6", projected_output_tokens=4096)

    def test_unknown_model_prices_at_the_most_expensive_tier(self) -> None:
        """A mis-configured model must never under-report spend."""
        guard = BudgetGuard(Settings(token_budget_usd=1000.0, token_budget_tokens=10_000_000))
        known = guard.charge("claude-haiku-4-5", Usage(output_tokens=1_000_000)).spent_usd
        guard2 = BudgetGuard(Settings(token_budget_usd=1000.0, token_budget_tokens=10_000_000))
        unknown = guard2.charge("totally-made-up-model", Usage(output_tokens=1_000_000)).spent_usd
        assert unknown > known

    async def test_run_aborts_with_budget_status(
        self, run_context: RunContext, settings: Settings
    ) -> None:
        """A supervisor whose budget cannot cover one call aborts with BUDGET_ABORT."""
        starved = settings.model_copy(update={"token_budget_usd": 0.000001})
        context = RunContext(
            run_id=run_context.run_id,
            settings=starved,
            engine=run_context.engine,
            mcp=run_context.mcp,
            bus=run_context.bus,
            tracer=run_context.tracer,
            budget=BudgetGuard(starved),
        )
        with use_context(context):
            update = await supervisor_node(_state(starved))

        assert update["status"] == RunStatus.BUDGET_ABORT
        assert "budget" in (update["abort_reason"] or "").lower()

    def test_snapshot_is_serialisable(self) -> None:
        guard = BudgetGuard(Settings())
        guard.charge("claude-haiku-4-5", Usage(input_tokens=5, output_tokens=5))
        payload = guard.snapshot().to_dict()
        assert payload["calls"] == 1
        assert payload["remaining_usd"] >= 0

    def test_spend_addition_is_lossless(self) -> None:
        total = TokenSpend()
        for _ in range(10):
            total = total.plus(TokenSpend(input_tokens=1, output_tokens=2, cost_usd=0.1, calls=1))
        assert total.input_tokens == 10
        assert total.output_tokens == 20
        assert total.calls == 10
        assert round(total.cost_usd, 6) == 1.0
