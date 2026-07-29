"""Independent recomputation of every metric, from raw state.

This module deliberately **does not import** ``app.mcp_server.metrics``. It
implements the same published definitions through different code:

===========================  ==============================  =========================
metric                       tool implementation             verifier implementation
===========================  ==============================  =========================
volatility                   two-pass mean / variance        Welford online variance
max drawdown                 explicit running-peak loop      ``itertools.accumulate``
30-day return baseline       forward scan with early break   ``bisect`` over dates
===========================  ==============================  =========================

Agreement between the two paths is asserted by a test against live market data.
Verification therefore checks the *number in the brief* against a figure this
module derives from the raw price bars — not against the figure the tool already
produced, and never against anything the model asserted.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, timedelta
from itertools import accumulate

from app.models.market import Fundamentals, PriceHistory, Sentiment

TRADING_DAYS_PER_YEAR = 252
RETURN_WINDOW_DAYS = 30


class RecomputationUnavailableError(RuntimeError):
    """Raised when raw state cannot support recomputing a claim at all."""


def _sorted_closes(history: PriceHistory) -> tuple[list[date], list[float]]:
    ordered = sorted(history.bars, key=lambda bar: bar.date)
    return [date.fromisoformat(bar.date[:10]) for bar in ordered], [bar.close for bar in ordered]


def _welford_variance(values: Sequence[float]) -> float | None:
    """Sample variance via Welford's online algorithm (ddof=1)."""
    count = 0
    mean = 0.0
    m2 = 0.0
    for value in values:
        count += 1
        delta = value - mean
        mean += delta / count
        m2 += delta * (value - mean)
    if count < 2:
        return None
    return m2 / (count - 1)


def _log_returns(closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    previous = None
    for close in closes:
        if previous is not None and previous > 0 and close > 0:
            out.append(math.log(close) - math.log(previous))
        previous = close
    return out


def recompute_last_close(history: PriceHistory) -> float | None:
    _, closes = _sorted_closes(history)
    return closes[-1] if closes else None


def recompute_previous_close(history: PriceHistory) -> float | None:
    _, closes = _sorted_closes(history)
    return closes[-2] if len(closes) >= 2 else None


def recompute_change_1d_pct(history: PriceHistory) -> float | None:
    _, closes = _sorted_closes(history)
    if len(closes) < 2 or closes[-2] <= 0:
        return None
    return (closes[-1] - closes[-2]) / closes[-2] * 100.0


def recompute_return_30d_pct(history: PriceHistory) -> float | None:
    """Trailing 30-calendar-day return, baseline located by binary search."""
    dates, closes = _sorted_closes(history)
    if len(closes) < 2:
        return None
    cutoff = dates[-1] - timedelta(days=RETURN_WINDOW_DAYS)
    index = bisect_right(dates, cutoff) - 1
    index = max(index, 0)
    if index >= len(closes) - 1:
        return None
    baseline = closes[index]
    if baseline <= 0:
        return None
    return (closes[-1] - baseline) / baseline * 100.0


def recompute_volatility_pct(history: PriceHistory) -> float | None:
    """Annualised volatility of daily log returns, via Welford variance."""
    _, closes = _sorted_closes(history)
    variance = _welford_variance(_log_returns(closes))
    if variance is None or variance < 0:
        return None
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def recompute_max_drawdown_pct(history: PriceHistory) -> float | None:
    """Largest peak-to-trough decline, via a running-maximum accumulation."""
    _, closes = _sorted_closes(history)
    if len(closes) < 2:
        return None
    peaks = list(accumulate(closes, max))
    ratios = [close / peak - 1.0 for close, peak in zip(closes, peaks, strict=True) if peak > 0]
    if not ratios:
        return None
    return min(*ratios, 0.0) * 100.0


def recompute_pe_ratio(fundamentals: Fundamentals | None) -> float | None:
    """P/E is reference data, not arithmetic: read straight from fundamentals."""
    if fundamentals is None or not fundamentals.ok:
        return None
    return fundamentals.pe_ratio


def recompute_sentiment(sentiment: Sentiment | None) -> float | None:
    if sentiment is None:
        return None
    return sentiment.score


def recompute(
    metric: str,
    *,
    history: PriceHistory | None,
    fundamentals: Fundamentals | None,
    sentiment: Sentiment | None,
) -> float | None:
    """Recompute one metric from raw state. ``None`` means "not derivable"."""
    if metric == "pe_ratio":
        return recompute_pe_ratio(fundamentals)
    if metric == "sentiment_score":
        return recompute_sentiment(sentiment)

    if history is None or not history.bars:
        return None

    handlers = {
        "last_close": recompute_last_close,
        "previous_close": recompute_previous_close,
        "change_1d_pct": recompute_change_1d_pct,
        "return_30d_pct": recompute_return_30d_pct,
        "volatility_annualised_pct": recompute_volatility_pct,
        "max_drawdown_pct": recompute_max_drawdown_pct,
    }
    handler = handlers.get(metric)
    if handler is None:
        raise RecomputationUnavailableError(f"no recomputation rule for metric '{metric}'")
    return handler(history)
