"""Typed, reducer-merged shared state. State design *is* the architecture.

The data agent and the news agent run in the same LangGraph superstep. Both write
into the same state object concurrently, so every field either belongs to exactly
one writer or carries an explicit reducer that merges concurrent updates without
losing either side.

Fields written by **both** workers — ``errors``, ``tool_calls``, ``attempts``,
``token_spend``, ``agents_completed`` — all have reducers. That is what makes the
parallel fan-out race-free: there is no read-modify-write anywhere, only
commutative merges applied by the graph runtime.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, TypedDict

from app.models.brief import Brief
from app.models.market import Fundamentals, Metrics, NewsFeed, PriceHistory, RiskEvent, Sentiment
from app.models.run import HumanDecision, RunError, RunMode, RunStatus, TokenSpend

#: Hard cap so a pathological run cannot grow state without bound.
MAX_TOOL_CALL_RECORDS = 2000
MAX_ERRORS = 500


# --------------------------------------------------------------------------- #
# Reducers                                                                     #
# --------------------------------------------------------------------------- #


def merge_mapping[VT](
    left: Mapping[str, VT] | None, right: Mapping[str, VT] | None
) -> dict[str, VT]:
    """Last-writer-wins per key. Disjoint keys from parallel agents both survive."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    merged = dict(left)
    merged.update(right)
    return merged


def merge_counters(
    left: Mapping[str, int] | None, right: Mapping[str, int] | None
) -> dict[str, int]:
    """Per-key integer addition — used for per-ticker attempt counts."""
    merged: dict[str, int] = dict(left or {})
    for key, value in (right or {}).items():
        merged[key] = merged.get(key, 0) + value
    return merged


def append_errors(
    left: Sequence[RunError] | None, right: Sequence[RunError] | None
) -> list[RunError]:
    """Append while de-duplicating identical (stage, ticker, message) triples."""
    merged: list[RunError] = list(left or [])
    seen = {error.key for error in merged}
    for error in right or []:
        if error.key in seen:
            continue
        seen.add(error.key)
        merged.append(error)
    return merged[-MAX_ERRORS:]


def append_records(
    left: Sequence[dict[str, Any]] | None, right: Sequence[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Plain append for telemetry rows, bounded to keep state small."""
    merged = list(left or []) + list(right or [])
    return merged[-MAX_TOOL_CALL_RECORDS:]


def append_risk_events(
    left: Sequence[RiskEvent] | None, right: Sequence[RiskEvent] | None
) -> list[RiskEvent]:
    """Append de-duplicating on (ticker, headline)."""
    merged: list[RiskEvent] = list(left or [])
    seen = {(event.ticker, event.headline) for event in merged}
    for event in right or []:
        key = (event.ticker, event.headline)
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    return merged


def append_unique_strings(left: Sequence[str] | None, right: Sequence[str] | None) -> list[str]:
    """Ordered set union."""
    merged = list(left or [])
    seen = set(merged)
    for value in right or []:
        if value not in seen:
            seen.add(value)
            merged.append(value)
    return merged


def merge_spend(left: TokenSpend | None, right: TokenSpend | None) -> TokenSpend:
    """Field-wise addition of token spend from concurrent model calls."""
    if left is None:
        return right or TokenSpend()
    if right is None:
        return left
    return left.plus(right)


def take_last[VT](left: VT | None, right: VT | None) -> VT | None:
    """Last non-None write wins. For fields only one node ever sets."""
    return right if right is not None else left


# --------------------------------------------------------------------------- #
# State                                                                        #
# --------------------------------------------------------------------------- #


class RunState(TypedDict, total=False):
    """Shared state for one brief."""

    # --- immutable run identity -------------------------------------------
    run_id: str
    watchlist_key: str
    tickers: list[str]
    mode: RunMode
    days: int
    news_limit: int
    session_date: str
    max_regenerations: int

    # --- reducer-merged worker output --------------------------------------
    prices: Annotated[dict[str, PriceHistory], merge_mapping]
    fundamentals: Annotated[dict[str, Fundamentals], merge_mapping]
    metrics: Annotated[dict[str, Metrics], merge_mapping]
    news: Annotated[dict[str, NewsFeed], merge_mapping]
    sentiment: Annotated[dict[str, Sentiment], merge_mapping]
    risk_events: Annotated[list[RiskEvent], append_risk_events]

    # --- reducer-merged cross-cutting --------------------------------------
    errors: Annotated[list[RunError], append_errors]
    tool_calls: Annotated[list[dict[str, Any]], append_records]
    attempts: Annotated[dict[str, int], merge_counters]
    agents_completed: Annotated[list[str], append_unique_strings]
    iterations: Annotated[int, operator.add]
    token_spend: Annotated[TokenSpend, merge_spend]

    # --- single-writer fields ----------------------------------------------
    plan: dict[str, Any]
    brief: Brief | None
    verification: dict[str, Any] | None
    regenerations: int
    status: RunStatus
    decision: HumanDecision | None
    delivery: dict[str, Any] | None
    abort_reason: str | None


def initial_state(
    *,
    run_id: str,
    watchlist_key: str,
    tickers: Sequence[str],
    session_date: str,
    mode: RunMode = RunMode.STANDARD,
    days: int = 120,
    news_limit: int = 6,
    max_regenerations: int = 1,
) -> RunState:
    """A fully-populated starting state — every channel initialised."""
    return RunState(
        run_id=run_id,
        watchlist_key=watchlist_key,
        tickers=list(tickers),
        mode=mode,
        days=days,
        news_limit=news_limit,
        session_date=session_date,
        max_regenerations=max_regenerations,
        prices={},
        fundamentals={},
        metrics={},
        news={},
        sentiment={},
        risk_events=[],
        errors=[],
        tool_calls=[],
        attempts={},
        agents_completed=[],
        iterations=0,
        token_spend=TokenSpend(),
        plan={},
        brief=None,
        verification=None,
        regenerations=0,
        status=RunStatus.RUNNING,
        decision=None,
        delivery=None,
        abort_reason=None,
    )


# --------------------------------------------------------------------------- #
# Completeness helpers — used by the supervisor to decide when to route on      #
# --------------------------------------------------------------------------- #


def has_market_data(state: RunState, ticker: str) -> bool:
    metrics = state.get("metrics", {}).get(ticker)
    return metrics is not None and metrics.ok


def has_news_data(state: RunState, ticker: str) -> bool:
    return ticker in state.get("sentiment", {})


def market_attempted(state: RunState, ticker: str) -> bool:
    return f"market:{ticker}" in state.get("attempts", {})


def news_attempted(state: RunState, ticker: str) -> bool:
    return f"news:{ticker}" in state.get("attempts", {})


def attempts_for(state: RunState, kind: str, ticker: str) -> int:
    return state.get("attempts", {}).get(f"{kind}:{ticker}", 0)


def pending_market_tickers(state: RunState, max_attempts: int = 2) -> list[str]:
    """Tickers still missing market data that have retries left."""
    return [
        ticker
        for ticker in state.get("tickers", [])
        if not has_market_data(state, ticker)
        and attempts_for(state, "market", ticker) < max_attempts
    ]


def pending_news_tickers(state: RunState, max_attempts: int = 2) -> list[str]:
    """Tickers still missing news/sentiment that have retries left."""
    return [
        ticker
        for ticker in state.get("tickers", [])
        if not has_news_data(state, ticker) and attempts_for(state, "news", ticker) < max_attempts
    ]


def completeness(state: RunState) -> dict[str, float]:
    """Per-ticker completeness in [0, 1] — drives the UI progress bars."""
    result: dict[str, float] = {}
    for ticker in state.get("tickers", []):
        score = 0.0
        if has_market_data(state, ticker):
            score += 0.6
        if has_news_data(state, ticker):
            score += 0.4
        result[ticker] = round(score, 2)
    return result


def usable_tickers(state: RunState) -> list[str]:
    """Tickers with enough data to appear as a full snapshot row."""
    return [t for t in state.get("tickers", []) if has_market_data(state, t)]


def is_partial(state: RunState) -> bool:
    """True when any ticker failed to produce market data."""
    return len(usable_tickers(state)) < len(state.get("tickers", []))
