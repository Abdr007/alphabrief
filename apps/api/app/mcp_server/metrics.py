"""Pure metric math — the only place returns, volatility and drawdown are computed.

Deliberately dependency-free (no pandas, no numpy) so every figure is exactly
reproducible from a list of floats, and so the independent verifier in
``app.graph.recompute`` can be written against the same raw inputs without
sharing any code path with this module.

Definitions are fixed and documented because the verifier must reproduce them
to the cent:

``return_30d_pct``
    Percentage change from the close of the last bar dated on or before
    (last trading date − 30 calendar days) to the most recent close.

``volatility_annualised_pct``
    Sample standard deviation (ddof=1) of daily log returns over the supplied
    window, scaled by sqrt(252), expressed in percent.

``max_drawdown_pct``
    Most negative value of (close / running peak close − 1) over the window,
    expressed in percent. Always ≤ 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from itertools import pairwise

TRADING_DAYS_PER_YEAR = 252
RETURN_WINDOW_DAYS = 30


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def daily_log_returns(closes: Sequence[float]) -> list[float]:
    """Log returns between consecutive closes; non-positive prices are skipped."""
    returns: list[float] = []
    for previous, current in pairwise(closes):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return returns


def sample_stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation (ddof=1). ``None`` for fewer than two points."""
    n = len(values)
    if n < 2:
        return None
    mean = math.fsum(values) / n
    variance = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def annualised_volatility_pct(closes: Sequence[float]) -> float | None:
    """Annualised volatility of daily log returns, in percent."""
    stdev = sample_stdev(daily_log_returns(closes))
    if stdev is None:
        return None
    return stdev * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def max_drawdown_pct(closes: Sequence[float]) -> float | None:
    """Largest peak-to-trough decline over the window, in percent (≤ 0)."""
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            worst = min(worst, close / peak - 1.0)
    return worst * 100.0


def return_over_window_pct(
    dates: Sequence[str],
    closes: Sequence[float],
    window_days: int = RETURN_WINDOW_DAYS,
) -> tuple[float | None, str | None]:
    """Percentage return over the trailing `window_days` calendar days.

    Returns ``(pct, baseline_date)``. The baseline is the last bar dated on or
    before ``last_date - window_days``; when the history does not reach that far
    back the earliest available bar is used instead.
    """
    if len(dates) < 2 or len(dates) != len(closes):
        return None, None
    last_date = _parse_date(dates[-1])
    cutoff = last_date - timedelta(days=window_days)

    baseline_index = 0
    for index, raw in enumerate(dates):
        if _parse_date(raw) <= cutoff:
            baseline_index = index
        else:
            break

    baseline_close = closes[baseline_index]
    if baseline_close <= 0:
        return None, None
    if baseline_index == len(closes) - 1:
        return None, None
    pct = (closes[-1] / baseline_close - 1.0) * 100.0
    return pct, dates[baseline_index]


def change_1d_pct(closes: Sequence[float]) -> float | None:
    """Percentage change between the last two closes."""
    if len(closes) < 2 or closes[-2] <= 0:
        return None
    return (closes[-1] / closes[-2] - 1.0) * 100.0


def round_metric(value: float | None, digits: int = 4) -> float | None:
    """Round for transport while keeping cent-level precision on prices."""
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)
