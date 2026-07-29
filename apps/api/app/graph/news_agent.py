"""News agent — retrieves headlines over MCP, then assesses them as data.

Runs in parallel with the data agent. Headlines are third-party text: they are
hardened and fenced inside ``<untrusted_data>`` before any model sees them, and
the agent's output is constrained to a fixed schema, so a headline cannot
redirect the run.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.core.budget import BudgetExceededError
from app.core.claude import AgentRole, LLMRequest, PromptHint
from app.core.events import EventKind
from app.core.security import fence_untrusted, harden_untrusted_text
from app.graph.context import RunContext, current_context
from app.graph.llm import call_model
from app.graph.prompts import EMIT_SENTIMENT_TOOL, NEWS_AGENT_SYSTEM
from app.graph.state import RunState
from app.mcp_server.client import McpToolError
from app.models.market import NewsFeed, RiskEvent, Sentiment
from app.models.run import RunError, RunStatus, Stage, TokenSpend

logger = logging.getLogger(__name__)

AGENT_NAME: Stage = "news_agent"
MAX_RISK_EVENTS_PER_TICKER = 4


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(score):
        return 0.0
    return max(-1.0, min(1.0, score))


def _match_headline(candidate: str, feed: NewsFeed | None) -> str | None:
    """Return the verbatim retrieved headline matching `candidate`, if any.

    Risk evidence must be a real headline. Anything the model paraphrases or
    invents fails this match and is dropped before it can reach the brief.
    """
    if not feed or not candidate:
        return None
    normalised = " ".join(candidate.split()).casefold()
    for item in feed.items:
        if " ".join(item.title.split()).casefold() == normalised:
            return item.title
    return None


async def _assess(
    ctx: RunContext, feeds: dict[str, NewsFeed]
) -> tuple[list[dict[str, Any]], TokenSpend]:
    """Ask the model to score sentiment over the retrieved headlines."""
    blocks: list[str] = []
    headlines_context: dict[str, list[str]] = {}
    for ticker, feed in feeds.items():
        titles = [item.title for item in feed.items]
        headlines_context[ticker] = titles
        blocks.append(f"{ticker}:\n{fence_untrusted(f'rss:{ticker}', titles)}")

    request = LLMRequest(
        role=AgentRole.NEWS,
        hint=PromptHint.NEWS_ASSESS,
        system=NEWS_AGENT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Assess sentiment and risk events for each ticker below, using only "
                    "the supplied headlines.\n\n" + "\n\n".join(blocks)
                ),
            }
        ],
        tools=[EMIT_SENTIMENT_TOOL],
        forced_tool=EMIT_SENTIMENT_TOOL.name,
        context={"headlines": headlines_context},
    )
    outcome = await call_model(ctx, request)
    payload = outcome.result.first_tool(EMIT_SENTIMENT_TOOL.name) or {}
    assessments = payload.get("assessments")
    return (assessments if isinstance(assessments, list) else []), outcome.spend


async def news_agent_node(state: RunState) -> dict[str, Any]:
    """Graph node: fetch headlines, score sentiment, extract risk events."""
    ctx = current_context()
    targets = list((state.get("plan") or {}).get("news_tickers", []))
    if not targets:
        return {"agents_completed": [AGENT_NAME]}

    await ctx.emit(
        EventKind.AGENT_STARTED,
        f"news_agent working {len(targets)} ticker(s)",
        {"agent": AGENT_NAME, "tickers": targets},
    )

    limit = int(state.get("news_limit", ctx.settings.news_limit))
    feeds: dict[str, NewsFeed] = {}
    errors: list[RunError] = []
    attempts: dict[str, int] = {f"news:{ticker}": 1 for ticker in targets}

    with (
        ctx.mcp.collect() as records,
        ctx.tracer.step("news_agent", run_id=ctx.run_id, input_data={"tickers": targets}) as span,
    ):
        for ticker in targets:
            try:
                feed = await ctx.mcp.fetch_rss_news(ticker, limit)
                feeds[ticker] = feed
                if feed.error:
                    errors.append(RunError(stage=AGENT_NAME, ticker=ticker, message=feed.error))
                elif feed.is_empty:
                    errors.append(
                        RunError(
                            stage=AGENT_NAME,
                            ticker=ticker,
                            message="no recent headlines published for this ticker",
                            severity="warning",
                        )
                    )
            except McpToolError as exc:
                logger.warning("news agent MCP failure for %s: %s", ticker, exc)
                errors.append(
                    RunError(
                        stage=AGENT_NAME, ticker=ticker, message=f"MCP tool unavailable: {exc}"
                    )
                )
            except Exception as exc:
                logger.exception("unexpected news agent failure for %s", ticker)
                errors.append(
                    RunError(
                        stage=AGENT_NAME,
                        ticker=ticker,
                        message=f"unexpected failure: {type(exc).__name__}",
                    )
                )

        sentiment: dict[str, Sentiment] = {}
        risk_events: list[RiskEvent] = []
        spend: TokenSpend | None = None

        if feeds:
            try:
                assessments, spend = await _assess(ctx, feeds)
            except BudgetExceededError as exc:
                return {
                    "news": feeds,
                    "errors": [*errors, RunError(stage=AGENT_NAME, message=str(exc))],
                    "attempts": attempts,
                    "agents_completed": [AGENT_NAME],
                    "status": RunStatus.BUDGET_ABORT,
                    "abort_reason": str(exc),
                }

            assigned = set(feeds)
            for entry in assessments:
                if not isinstance(entry, dict):
                    continue
                ticker = str(entry.get("ticker", "")).strip().upper()
                if ticker not in assigned:
                    continue
                feed = feeds[ticker]
                sentiment[ticker] = Sentiment(
                    ticker=ticker,
                    score=_clamp_score(entry.get("score")),
                    reasoning=harden_untrusted_text(
                        str(entry.get("reasoning") or "No reasoning supplied."), max_chars=300
                    ),
                    headline_count=len(feed.items),
                )
                for raw_event in (entry.get("risk_events") or [])[:MAX_RISK_EVENTS_PER_TICKER]:
                    if not isinstance(raw_event, dict):
                        continue
                    verbatim = _match_headline(str(raw_event.get("headline", "")), feed)
                    if verbatim is None:
                        # Not a real retrieved headline — refuse to carry it forward.
                        errors.append(
                            RunError(
                                stage=AGENT_NAME,
                                ticker=ticker,
                                message="dropped a risk event whose headline was not retrieved",
                                severity="warning",
                            )
                        )
                        continue
                    risk_events.append(
                        RiskEvent(
                            ticker=ticker,
                            category=harden_untrusted_text(
                                str(raw_event.get("category") or "unclassified"), max_chars=40
                            ),
                            headline=verbatim,
                        )
                    )

            # Any ticker the model skipped still needs a neutral, explicit entry.
            for ticker, feed in feeds.items():
                if ticker in sentiment:
                    continue
                sentiment[ticker] = Sentiment(
                    ticker=ticker,
                    score=0.0,
                    reasoning="No sentiment returned for this ticker; recorded as neutral.",
                    headline_count=len(feed.items),
                )

        span.update(
            output={
                "tickers_scored": sorted(sentiment),
                "risk_events": len(risk_events),
                "tool_calls": len(records),
            }
        )
        tool_call_rows = [record.to_dict() for record in records]

    await ctx.emit(
        EventKind.AGENT_COMPLETED,
        f"news_agent finished — {len(sentiment)} scored, {len(risk_events)} risk event(s)",
        {"agent": AGENT_NAME, "scored": len(sentiment), "risk_events": len(risk_events)},
    )

    update: dict[str, Any] = {
        "news": feeds,
        "sentiment": sentiment,
        "risk_events": risk_events,
        "errors": errors,
        "attempts": attempts,
        "tool_calls": tool_call_rows,
        "agents_completed": [AGENT_NAME],
    }
    if spend is not None:
        update["token_spend"] = spend
    return update
