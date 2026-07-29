"""Checkpoint serialisation with an explicit type allowlist.

LangGraph's default checkpoint serializer is permissive: it will reconstruct any
type found in a checkpoint and emits a deprecation warning while doing so. That
is both a warning we refuse to ship and a needlessly wide deserialisation
surface — checkpoint bytes should only ever rehydrate into types this
application actually stores.

So the serializer is built in **strict** mode and handed the exact set of models
that appear in :class:`~app.graph.state.RunState`. Anything else in a checkpoint
is rejected rather than instantiated.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.graph.verify import ClaimCheck, QuoteCheck, VerificationReport
from app.models.brief import (
    Brief,
    KeyMove,
    NewsAndSentiment,
    NumericClaim,
    RiskFlag,
    SnapshotRow,
    WatchItem,
)
from app.models.market import (
    Fundamentals,
    Metrics,
    NewsFeed,
    NewsItem,
    PriceBar,
    PriceHistory,
    RiskEvent,
    Sentiment,
)
from app.models.run import HumanDecision, RunError, RunMode, RunStatus, TokenSpend

#: Every type that can legitimately appear inside a checkpointed RunState.
CHECKPOINT_TYPES: tuple[type[Any], ...] = (
    # market data
    PriceBar,
    PriceHistory,
    Fundamentals,
    Metrics,
    NewsItem,
    NewsFeed,
    Sentiment,
    RiskEvent,
    # brief
    Brief,
    NumericClaim,
    SnapshotRow,
    KeyMove,
    NewsAndSentiment,
    RiskFlag,
    WatchItem,
    # verification
    VerificationReport,
    ClaimCheck,
    QuoteCheck,
    # run bookkeeping
    RunError,
    TokenSpend,
    HumanDecision,
    RunStatus,
    RunMode,
)


def build_checkpoint_serde() -> JsonPlusSerializer:
    """A strict serializer that only rehydrates AlphaBrief's own models."""
    strict = JsonPlusSerializer(allowed_msgpack_modules=None)
    return strict.with_msgpack_allowlist(list(CHECKPOINT_TYPES))
