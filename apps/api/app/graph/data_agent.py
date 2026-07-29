"""Data agent — decides what to compute, then consumes MCP tools to get it.

Runs in parallel with the news agent. Every number it contributes to state came
out of the MCP tool server; the agent's own contribution is the *plan*.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.budget import BudgetExceededError
from app.core.claude import AgentRole, LLMRequest, PromptHint
from app.core.events import EventKind
from app.graph.context import RunContext, current_context
from app.graph.llm import call_model
from app.graph.prompts import DATA_AGENT_SYSTEM, PLAN_MARKET_DATA_TOOL
from app.graph.state import RunState
from app.mcp_server.client import McpToolError
from app.mcp_server.providers import MAX_HISTORY_DAYS
from app.models.market import Fundamentals, Metrics, PriceHistory
from app.models.run import RunError, RunStatus, Stage

logger = logging.getLogger(__name__)

AGENT_NAME: Stage = "data_agent"


async def _plan_targets(
    ctx: RunContext, state: RunState, targets: list[str]
) -> tuple[list[str], int, dict[str, Any]]:
    """Ask the model which tickers to pull and over what window."""
    default_days = int(state.get("days", ctx.settings.price_history_days))
    request = LLMRequest(
        role=AgentRole.DATA,
        hint=PromptHint.DATA_ANALYSE,
        system=DATA_AGENT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Assigned tickers: {', '.join(targets)}.\n"
                    f"Default history window: {default_days} calendar days.\n"
                    "Call plan_market_data with the tickers to fetch and the window to use."
                ),
            }
        ],
        tools=[PLAN_MARKET_DATA_TOOL],
        forced_tool=PLAN_MARKET_DATA_TOOL.name,
        context={"tickers": targets, "days": default_days},
    )
    outcome = await call_model(ctx, request)
    plan = outcome.result.first_tool(PLAN_MARKET_DATA_TOOL.name) or {}

    # Scope containment: the model may narrow the assignment but never widen it.
    # A prompt-injected "also fetch XYZ" therefore cannot reach the provider.
    assigned = set(targets)
    requested = [
        str(t).strip().upper()
        for t in plan.get("tickers", [])
        if str(t).strip().upper() in assigned
    ]
    chosen = requested or targets

    days = plan.get("days", default_days)
    try:
        days_int = max(2, min(int(days), MAX_HISTORY_DAYS))
    except (TypeError, ValueError):
        days_int = default_days

    spend = outcome.spend.model_dump()
    return chosen, days_int, spend


async def data_agent_node(state: RunState) -> dict[str, Any]:
    """Graph node: fetch prices, fundamentals and metrics for the assigned tickers."""
    ctx = current_context()
    targets = list((state.get("plan") or {}).get("market_tickers", []))
    if not targets:
        return {"agents_completed": [AGENT_NAME]}

    await ctx.emit(
        EventKind.AGENT_STARTED,
        f"data_agent working {len(targets)} ticker(s)",
        {"agent": AGENT_NAME, "tickers": targets},
    )

    prices: dict[str, PriceHistory] = {}
    fundamentals: dict[str, Fundamentals] = {}
    metrics: dict[str, Metrics] = {}
    errors: list[RunError] = []
    attempts: dict[str, int] = {f"market:{ticker}": 1 for ticker in targets}
    spend_delta: dict[str, Any] = {}

    with (
        ctx.mcp.collect() as records,
        ctx.tracer.step("data_agent", run_id=ctx.run_id, input_data={"tickers": targets}) as span,
    ):
        try:
            chosen, days, spend_delta = await _plan_targets(ctx, state, targets)
        except BudgetExceededError as exc:
            return {
                "agents_completed": [AGENT_NAME],
                "attempts": attempts,
                "status": RunStatus.BUDGET_ABORT,
                "abort_reason": str(exc),
                "errors": [RunError(stage=AGENT_NAME, message=str(exc))],
            }

        for ticker in chosen:
            try:
                history = await ctx.mcp.get_price_history(ticker, days)
                prices[ticker] = history

                company = await ctx.mcp.get_fundamentals(ticker)
                fundamentals[ticker] = company

                computed = await ctx.mcp.compute_metrics(
                    ticker, list(history.bars), company.pe_ratio
                )
                metrics[ticker] = computed

                if history.error:
                    errors.append(RunError(stage=AGENT_NAME, ticker=ticker, message=history.error))
                if company.error:
                    errors.append(
                        RunError(
                            stage=AGENT_NAME,
                            ticker=ticker,
                            message=company.error,
                            severity="warning",
                        )
                    )
                if computed.error:
                    errors.append(RunError(stage=AGENT_NAME, ticker=ticker, message=computed.error))
            except McpToolError as exc:
                logger.warning("data agent MCP failure for %s: %s", ticker, exc)
                errors.append(
                    RunError(
                        stage=AGENT_NAME,
                        ticker=ticker,
                        message=f"MCP tool unavailable: {exc}",
                    )
                )
            except Exception as exc:
                logger.exception("unexpected data agent failure for %s", ticker)
                errors.append(
                    RunError(
                        stage=AGENT_NAME,
                        ticker=ticker,
                        message=f"unexpected failure: {type(exc).__name__}",
                    )
                )

        span.update(
            output={
                "tickers_completed": sorted(metrics),
                "errors": len(errors),
                "tool_calls": len(records),
            }
        )
        tool_call_rows = [record.to_dict() for record in records]

    ok_count = sum(1 for metric in metrics.values() if metric.ok)
    await ctx.emit(
        EventKind.AGENT_COMPLETED,
        f"data_agent finished — {ok_count}/{len(targets)} ticker(s) with metrics",
        {"agent": AGENT_NAME, "ok": ok_count, "errors": len(errors)},
    )

    update: dict[str, Any] = {
        "prices": prices,
        "fundamentals": fundamentals,
        "metrics": metrics,
        "errors": errors,
        "attempts": attempts,
        "tool_calls": tool_call_rows,
        "agents_completed": [AGENT_NAME],
    }
    if spend_delta:
        from app.models.run import TokenSpend

        update["token_spend"] = TokenSpend.model_validate(spend_delta)
    return update
