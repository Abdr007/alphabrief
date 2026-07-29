"""Supervisor — plans, fans out in parallel, retries once, and knows when to stop.

Two independent brakes guarantee termination:

* a hard iteration cap (default 15), and
* the per-run budget guard, which trips ``BUDGET_ABORT`` from inside any node.

A ticker gets exactly one retry. Once its attempt count reaches two it is dropped
from the pending set, so a permanently broken symbol can never loop the graph.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END

from app.core.budget import BudgetExceededError
from app.core.claude import AgentRole, LLMRequest, PromptHint
from app.core.events import EventKind
from app.graph.context import current_context
from app.graph.llm import call_model
from app.graph.prompts import SUPERVISOR_SYSTEM
from app.graph.state import (
    RunState,
    completeness,
    pending_market_tickers,
    pending_news_tickers,
)
from app.models.run import RunError, RunStatus

logger = logging.getLogger(__name__)

MAX_ATTEMPTS_PER_TICKER = 2
DispatchTarget = Literal["data_agent", "news_agent", "writer", "__end__"]


async def supervisor_node(state: RunState) -> dict[str, Any]:
    """Graph node: decide what still needs doing and dispatch the workers."""
    ctx = current_context()
    settings = ctx.settings

    current_iteration = int(state.get("iterations", 0))
    next_iteration = current_iteration + 1

    # ---- brake 1: hard iteration cap -------------------------------------
    if next_iteration > settings.max_iterations:
        reason = (
            f"supervisor hit the hard iteration cap of {settings.max_iterations} "
            f"without completing the watchlist"
        )
        await ctx.emit(EventKind.RUN_FAILED, reason, {"iterations": current_iteration})
        return {
            "status": RunStatus.ITERATION_ABORT,
            "abort_reason": reason,
            "errors": [RunError(stage="supervisor", message=reason)],
            "plan": {"dispatch": [], "market_tickers": [], "news_tickers": []},
        }

    # ---- brake 2: a worker already tripped the budget guard ---------------
    if state.get("status") in (RunStatus.BUDGET_ABORT, RunStatus.ITERATION_ABORT):
        return {"plan": {"dispatch": [], "market_tickers": [], "news_tickers": []}}

    market_pending = pending_market_tickers(state, MAX_ATTEMPTS_PER_TICKER)
    news_pending = pending_news_tickers(state, MAX_ATTEMPTS_PER_TICKER)

    dispatch: list[str] = []
    if market_pending:
        dispatch.append("data_agent")
    if news_pending:
        dispatch.append("news_agent")

    plan: dict[str, Any] = {
        "dispatch": dispatch,
        "market_tickers": market_pending,
        "news_tickers": news_pending,
        "iteration": next_iteration,
    }

    update: dict[str, Any] = {"iterations": 1, "plan": plan}

    # The supervisor is routed by Haiku for cost discipline. Its reasoning is
    # advisory: the dispatch set itself is computed from state, so a model hiccup
    # can slow the run down but can never send it somewhere unsafe.
    if dispatch:
        try:
            outcome = await call_model(
                ctx,
                LLMRequest(
                    role=AgentRole.SUPERVISOR,
                    hint=PromptHint.SUPERVISOR_PLAN,
                    system=SUPERVISOR_SYSTEM,
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"Iteration {next_iteration} of {settings.max_iterations}.\n"
                                f"Watchlist: {', '.join(state.get('tickers', []))}\n"
                                f"Missing market data: {', '.join(market_pending) or 'none'}\n"
                                f"Missing news/sentiment: {', '.join(news_pending) or 'none'}\n"
                                "State one sentence on what you are dispatching and why."
                            ),
                        }
                    ],
                    max_tokens=300,
                    context={"pending_tickers": sorted(set(market_pending) | set(news_pending))},
                ),
            )
            plan["reason"] = outcome.result.text[:400]
            update["token_spend"] = outcome.spend
        except BudgetExceededError as exc:
            await ctx.emit(EventKind.RUN_FAILED, "Budget exhausted during planning", {})
            return {
                "status": RunStatus.BUDGET_ABORT,
                "abort_reason": str(exc),
                "errors": [RunError(stage="supervisor", message=str(exc))],
                "plan": {"dispatch": [], "market_tickers": [], "news_tickers": []},
            }

    await ctx.emit(
        EventKind.SUPERVISOR_PLAN,
        (
            f"Dispatching {' + '.join(dispatch)} (parallel)"
            if len(dispatch) > 1
            else (
                f"Dispatching {dispatch[0]}"
                if dispatch
                else "Watchlist complete — routing to writer"
            )
        ),
        {
            "iteration": next_iteration,
            "dispatch": dispatch,
            "market_tickers": market_pending,
            "news_tickers": news_pending,
            "reason": plan.get("reason"),
        },
    )
    await ctx.emit(
        EventKind.STATE_PROGRESS,
        "State completeness",
        {"completeness": completeness(state), "iteration": next_iteration},
    )
    return update


def route_from_supervisor(state: RunState) -> list[str] | str:
    """Conditional edge: parallel fan-out, straight to the writer, or abort.

    Returning a *list* of node names is what makes the two workers run in the
    same LangGraph superstep — that is the parallelism the reducers exist for.
    """
    status = state.get("status")
    if status in (RunStatus.BUDGET_ABORT, RunStatus.ITERATION_ABORT):
        return END

    dispatch = list((state.get("plan") or {}).get("dispatch", []))
    if not dispatch:
        return "writer"
    return dispatch
