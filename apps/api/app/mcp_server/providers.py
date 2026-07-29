"""Free, keyless data providers behind the MCP tools.

Market data: ``yfinance``. News: public RSS (Yahoo Finance + Google News) parsed
with ``feedparser``. No API keys, no quotas, no signup — which is what keeps the
whole project at ``$0`` and makes demos quota-anxiety-free.

Every provider call is:

* **cached per run** — a run never asks the same provider the same question twice;
* **politely rate limited** — a minimum interval between outbound calls;
* **failure-tolerant** — network, parse and empty-result failures are converted
  into a structured ``error`` on the returned model, never an exception.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from app.models.market import Fundamentals, NewsFeed, NewsItem, PriceBar, PriceHistory

logger = logging.getLogger(__name__)

MAX_HISTORY_DAYS: Final = 3650
MAX_NEWS_LIMIT: Final = 25
TICKER_MAX_LEN: Final = 12
HTTP_TIMEOUT_SECONDS: Final = 15.0
USER_AGENT: Final = "AlphaBrief/1.0 (+https://alphabrief.vercel.app) research-brief-bot"

YAHOO_RSS: Final = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
)
GOOGLE_NEWS_RSS: Final = (
    "https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
)


def normalise_ticker(raw: str) -> str:
    """Uppercase, trim and length-bound a ticker symbol.

    Symbols are used to build outbound URLs, so anything outside a conservative
    character set is rejected rather than escaped.
    """
    ticker = (raw or "").strip().upper()
    if not ticker:
        raise ValueError("ticker must not be empty")
    if len(ticker) > TICKER_MAX_LEN:
        raise ValueError(f"ticker too long (max {TICKER_MAX_LEN} characters)")
    if not all(ch.isalnum() or ch in {".", "-", "^"} for ch in ticker):
        raise ValueError("ticker contains unsupported characters")
    return ticker


@dataclass(slots=True)
class ProviderContext:
    """Per-run cache and polite rate limiter shared by all tools in one server."""

    min_interval_seconds: float = 0.20
    _cache: dict[str, Any] = field(default_factory=dict)
    _last_call: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    hits: int = 0
    misses: int = 0

    async def throttle(self) -> None:
        """Enforce a minimum gap between outbound provider calls."""
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval_seconds - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    def get(self, key: str) -> Any | None:
        value = self._cache.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: str, value: Any) -> None:
        self._cache[key] = value

    def stats(self) -> dict[str, int]:
        return {"cache_hits": self.hits, "cache_misses": self.misses, "entries": len(self._cache)}


# --------------------------------------------------------------------------- #
# Market data                                                                  #
# --------------------------------------------------------------------------- #


def _fetch_history_blocking(ticker: str, days: int) -> list[PriceBar]:
    """Blocking yfinance call, executed in a worker thread."""
    import yfinance as yf

    end = datetime.now(UTC).date() + timedelta(days=1)
    # Pad the window so `days` calendar days still yields enough trading bars.
    start = end - timedelta(days=days + 12)
    frame = yf.Ticker(ticker).history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
        raise_errors=False,
    )
    if frame is None or getattr(frame, "empty", True):
        return []

    bars: list[PriceBar] = []
    for index, row in frame.iterrows():
        try:
            close = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isnan(close):
            continue
        bars.append(
            PriceBar(
                date=str(index)[:10],
                open=float(row.get("Open", close) or close),
                high=float(row.get("High", close) or close),
                low=float(row.get("Low", close) or close),
                close=close,
                volume=float(row.get("Volume", 0) or 0),
            )
        )
    return bars


async def fetch_price_history(ctx: ProviderContext, ticker: str, days: int) -> PriceHistory:
    """Daily OHLCV history for `ticker` over the trailing `days` calendar days."""
    try:
        symbol = normalise_ticker(ticker)
    except ValueError as exc:
        return PriceHistory(ticker=ticker, days_requested=days, error=str(exc))

    window = max(2, min(int(days), MAX_HISTORY_DAYS))
    key = f"history:{symbol}:{window}"
    cached = ctx.get(key)
    if isinstance(cached, PriceHistory):
        return cached

    await ctx.throttle()
    try:
        bars = await asyncio.to_thread(_fetch_history_blocking, symbol, window)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the run
        logger.warning("price history failed for %s: %s", symbol, exc)
        result = PriceHistory(
            ticker=symbol,
            days_requested=window,
            error=f"price provider unavailable: {type(exc).__name__}",
        )
        ctx.put(key, result)
        return result

    if not bars:
        result = PriceHistory(
            ticker=symbol,
            days_requested=window,
            error="no price data returned (unknown or delisted ticker)",
        )
    else:
        result = PriceHistory(ticker=symbol, days_requested=window, bars=bars)
    ctx.put(key, result)
    return result


def _fetch_fundamentals_blocking(ticker: str) -> dict[str, Any]:
    """Blocking yfinance metadata call, executed in a worker thread."""
    import yfinance as yf

    handle = yf.Ticker(ticker)
    info: dict[str, Any] = {}
    try:
        raw = handle.get_info()
        if isinstance(raw, dict):
            info = raw
    except Exception:
        logger.debug("get_info failed for %s", ticker, exc_info=True)

    if not info:
        try:
            fast = handle.fast_info
            info = {
                "marketCap": fast.get("market_cap") if hasattr(fast, "get") else None,
                "currency": fast.get("currency") if hasattr(fast, "get") else None,
            }
        except Exception:
            logger.debug("fast_info failed for %s", ticker, exc_info=True)
            info = {}
    return info


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


async def fetch_fundamentals(ctx: ProviderContext, ticker: str) -> Fundamentals:
    """Company reference data (name, sector, P/E, market cap)."""
    try:
        symbol = normalise_ticker(ticker)
    except ValueError as exc:
        return Fundamentals(ticker=ticker, error=str(exc))

    key = f"fundamentals:{symbol}"
    cached = ctx.get(key)
    if isinstance(cached, Fundamentals):
        return cached

    await ctx.throttle()
    try:
        info = await asyncio.to_thread(_fetch_fundamentals_blocking, symbol)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the run
        logger.warning("fundamentals failed for %s: %s", symbol, exc)
        result = Fundamentals(
            ticker=symbol, error=f"fundamentals provider unavailable: {type(exc).__name__}"
        )
        ctx.put(key, result)
        return result

    candidate = Fundamentals(
        ticker=symbol,
        name=(info.get("shortName") or info.get("longName") or None),
        sector=info.get("sector") or None,
        currency=str(info.get("currency") or "USD").upper(),
        pe_ratio=_as_float(info.get("trailingPE")),
        forward_pe=_as_float(info.get("forwardPE")),
        market_cap=_as_float(info.get("marketCap")),
    )
    # yfinance returns a non-empty dict of residual keys for unknown symbols, so
    # "the provider replied" is not the same as "this ticker exists". Require at
    # least one identifying field before treating the payload as usable.
    identifying = (
        candidate.name,
        candidate.sector,
        candidate.market_cap,
        candidate.pe_ratio,
        candidate.forward_pe,
    )
    if not info or all(field_value is None for field_value in identifying):
        result = Fundamentals(
            ticker=symbol,
            error="no fundamentals returned (unknown or delisted ticker)",
        )
    else:
        result = candidate
    ctx.put(key, result)
    return result


# --------------------------------------------------------------------------- #
# News                                                                         #
# --------------------------------------------------------------------------- #


def _parse_feed(payload: bytes, source: str) -> list[NewsItem]:
    import feedparser

    parsed = feedparser.parse(payload)
    items: list[NewsItem] = []
    for entry in getattr(parsed, "entries", []) or []:
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            NewsItem(
                title=title,
                link=str(entry.get("link") or "")[:500],
                published=str(entry.get("published") or entry.get("updated") or "") or None,
                source=source,
            )
        )
    return items


async def fetch_rss_news(ctx: ProviderContext, ticker: str, limit: int) -> NewsFeed:
    """Recent headlines for `ticker`, merged from Yahoo Finance and Google News."""
    try:
        symbol = normalise_ticker(ticker)
    except ValueError as exc:
        return NewsFeed(ticker=ticker, error=str(exc))

    capped = max(1, min(int(limit), MAX_NEWS_LIMIT))
    key = f"news:{symbol}:{capped}"
    cached = ctx.get(key)
    if isinstance(cached, NewsFeed):
        return cached

    feeds = [
        ("yahoo-finance", YAHOO_RSS.format(ticker=symbol)),
        ("google-news", GOOGLE_NEWS_RSS.format(ticker=symbol)),
    ]
    items: list[NewsItem] = []
    failures: list[str] = []

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
        follow_redirects=True,
    ) as client:
        for source, url in feeds:
            await ctx.throttle()
            try:
                response = await client.get(url)
                response.raise_for_status()
                items.extend(_parse_feed(response.content, source))
            except Exception as exc:  # noqa: BLE001 - one dead feed must not kill the run
                logger.warning("rss fetch failed for %s via %s: %s", symbol, source, exc)
                failures.append(f"{source}: {type(exc).__name__}")

    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        fingerprint = item.title.casefold()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(item)
        if len(deduped) >= capped:
            break

    error: str | None = None
    if not deduped and len(failures) == len(feeds):
        error = "all news feeds unavailable: " + "; ".join(failures)

    result = NewsFeed(
        ticker=symbol,
        items=deduped,
        feeds_queried=[source for source, _ in feeds],
        error=error,
    )
    ctx.put(key, result)
    return result
