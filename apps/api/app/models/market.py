"""Typed contracts for everything the MCP tool server returns.

These models are the *only* representation of market data in the system. The
graph stores them, the writer reads them, and the verifier recomputes from them —
so a number can always be traced back to the exact price bar it came from.

Every model carries an ``error`` field instead of raising. A bad ticker, a dead
network or an empty feed therefore produces a structured, per-ticker failure that
the run can degrade around rather than an exception that kills it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MetricName = Literal[
    "last_close",
    "previous_close",
    "change_1d_pct",
    "return_30d_pct",
    "volatility_annualised_pct",
    "max_drawdown_pct",
    "pe_ratio",
    "sentiment_score",
]

#: Units drive the verifier's comparison tolerance.
MetricUnit = Literal["usd", "percent", "ratio", "score"]

METRIC_UNITS: dict[str, MetricUnit] = {
    "last_close": "usd",
    "previous_close": "usd",
    "change_1d_pct": "percent",
    "return_30d_pct": "percent",
    "volatility_annualised_pct": "percent",
    "max_drawdown_pct": "percent",
    "pe_ratio": "ratio",
    "sentiment_score": "score",
}


class StrictModel(BaseModel):
    """Base with extra fields forbidden — schema drift fails loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceBar(StrictModel):
    """One daily OHLCV bar."""

    date: str = Field(description="Trading date, ISO-8601 (YYYY-MM-DD).")
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceHistory(StrictModel):
    """Daily price history for one ticker."""

    ticker: str
    days_requested: int
    bars: list[PriceBar] = Field(default_factory=list)
    currency: str = "USD"
    source: str = "yfinance"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.bars) >= 2


class Fundamentals(StrictModel):
    """Slow-moving reference data for one ticker."""

    ticker: str
    name: str | None = None
    sector: str | None = None
    currency: str = "USD"
    pe_ratio: float | None = None
    forward_pe: float | None = None
    market_cap: float | None = None
    source: str = "yfinance"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Metrics(StrictModel):
    """Tool-computed metrics. The LLM never produces these numbers."""

    ticker: str
    last_close: float | None = None
    previous_close: float | None = None
    change_1d_pct: float | None = None
    return_30d_pct: float | None = None
    volatility_annualised_pct: float | None = None
    max_drawdown_pct: float | None = None
    pe_ratio: float | None = None
    sample_size: int = 0
    window_start: str | None = None
    window_end: str | None = None
    #: Date of the bar used as the 30-day baseline, so the verifier can reproduce it.
    return_30d_baseline_date: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.last_close is not None

    def value_of(self, metric: str) -> float | None:
        return {
            "last_close": self.last_close,
            "previous_close": self.previous_close,
            "change_1d_pct": self.change_1d_pct,
            "return_30d_pct": self.return_30d_pct,
            "volatility_annualised_pct": self.volatility_annualised_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "pe_ratio": self.pe_ratio,
        }.get(metric)


class NewsItem(StrictModel):
    """One headline from a public RSS feed."""

    title: str
    link: str = ""
    published: str | None = None
    source: str = ""


class NewsFeed(StrictModel):
    """Headlines for one ticker, merged across the configured feeds."""

    ticker: str
    items: list[NewsItem] = Field(default_factory=list)
    feeds_queried: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def is_empty(self) -> bool:
        return self.error is None and not self.items


class Sentiment(StrictModel):
    """Per-ticker sentiment with explicit reasoning."""

    ticker: str
    score: float = Field(ge=-1.0, le=1.0)
    reasoning: str
    headline_count: int = 0

    @property
    def label(self) -> Literal["negative", "neutral", "positive"]:
        if self.score <= -0.15:
            return "negative"
        if self.score >= 0.15:
            return "positive"
        return "neutral"


class RiskEvent(StrictModel):
    """A flagged risk headline."""

    ticker: str
    category: str
    headline: str
