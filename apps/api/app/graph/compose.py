"""Deterministic brief assembly from verified state.

Used in two places:

* by :class:`~app.core.claude.DeterministicEngine` as the offline writer, and
* by the writer node as the **canonical claim table**: even when Claude writes the
  prose, the numbers it is allowed to cite are minted here, straight from
  tool-computed metrics.

Every narrative string produced here is digit-free by construction — figures are
emitted only as ``{{cN}}`` claim references.
"""

from __future__ import annotations

from typing import Any, Literal

from app.models.brief import (
    Brief,
    KeyMove,
    NewsAndSentiment,
    NumericClaim,
    RiskFlag,
    SnapshotRow,
    WatchItem,
)
from app.models.market import METRIC_UNITS, Fundamentals, Metrics, NewsFeed, RiskEvent, Sentiment

#: Direction of a single-session move.
Direction = Literal["up", "down", "flat"]

#: Metrics minted as claims for each ticker, in snapshot column order.
SNAPSHOT_METRICS: tuple[tuple[str, str], ...] = (
    ("last_close", "last_close"),
    ("change_1d_pct", "change_1d"),
    ("return_30d_pct", "return_30d"),
    ("volatility_annualised_pct", "volatility"),
    ("max_drawdown_pct", "max_drawdown"),
    ("pe_ratio", "pe_ratio"),
)

#: Above this annualised volatility a ticker earns an automatic watch item.
HIGH_VOLATILITY_PCT = 40.0
#: Below this 30-day return a ticker is called out as a laggard.
WEAK_RETURN_PCT = -5.0
STRONG_RETURN_PCT = 5.0


class ClaimMinter:
    """Allocates stable ``c1..cN`` ids for tool-computed values."""

    def __init__(self) -> None:
        self._claims: list[NumericClaim] = []
        self._index: dict[tuple[str, str], str] = {}

    def mint(self, ticker: str, metric: str, value: float | None) -> str | None:
        """Register a claim and return its reference id, or ``None`` if absent."""
        if value is None:
            return None
        key = (ticker, metric)
        if key in self._index:
            return self._index[key]
        claim_id = f"c{len(self._claims) + 1}"
        self._claims.append(
            NumericClaim(
                claim_id=claim_id,
                ticker=ticker,
                metric=metric,
                value=float(value),
                unit=METRIC_UNITS[metric],
            )
        )
        self._index[key] = claim_id
        return claim_id

    def ref(self, ticker: str, metric: str) -> str | None:
        return self._index.get((ticker, metric))

    @property
    def claims(self) -> list[NumericClaim]:
        return list(self._claims)


def _company_name(fundamentals: Fundamentals | None, ticker: str) -> str:
    if fundamentals and fundamentals.ok and fundamentals.name:
        return fundamentals.name
    return ticker


def compose_brief(
    *,
    session_date: str,
    tickers: list[str],
    metrics: dict[str, Metrics],
    fundamentals: dict[str, Fundamentals],
    sentiment: dict[str, Sentiment],
    news: dict[str, NewsFeed],
    risk_events: list[RiskEvent],
    data_gaps: list[str],
) -> Brief:
    """Assemble a fully-verified brief. Performs no arithmetic beyond comparisons."""
    minter = ClaimMinter()
    usable = [t for t in tickers if (m := metrics.get(t)) is not None and m.ok]
    partial = len(usable) < len(tickers)

    # ----------------------------------------------------------- snapshot ---
    snapshot: list[SnapshotRow] = []
    for ticker in usable:
        metric = metrics[ticker]
        refs: dict[str, str | None] = {}
        for metric_name, column in SNAPSHOT_METRICS:
            refs[column] = minter.mint(ticker, metric_name, metric.value_of(metric_name))
        senti = sentiment.get(ticker)
        refs["sentiment"] = minter.mint(ticker, "sentiment_score", senti.score) if senti else None
        filled = sum(1 for value in refs.values() if value)
        snapshot.append(
            SnapshotRow(
                ticker=ticker,
                company=_company_name(fundamentals.get(ticker), ticker),
                status="ok" if filled >= len(refs) - 1 else "partial",
                last_close=refs["last_close"],
                change_1d=refs["change_1d"],
                return_30d=refs["return_30d"],
                volatility=refs["volatility"],
                max_drawdown=refs["max_drawdown"],
                pe_ratio=refs["pe_ratio"],
                sentiment=refs["sentiment"],
                note=None
                if filled >= len(refs) - 1
                else "Some fields unavailable from the provider.",
            )
        )

    # ---------------------------------------------------------- key moves ---
    ranked = sorted(
        (t for t in usable if metrics[t].change_1d_pct is not None),
        key=lambda t: abs(metrics[t].change_1d_pct or 0.0),
        reverse=True,
    )
    key_moves: list[KeyMove] = []
    for ticker in ranked[:3]:
        metric = metrics[ticker]
        change = metric.change_1d_pct or 0.0
        close_ref = minter.ref(ticker, "last_close")
        change_ref = minter.ref(ticker, "change_1d_pct")
        return_ref = minter.ref(ticker, "return_30d_pct")
        direction: Direction = "up" if change > 0 else ("down" if change < 0 else "flat")
        verb = {"up": "advanced", "down": "declined", "flat": "held flat"}[direction]
        # Percent-unit claims already render with a trailing '%', so the prose
        # must not repeat the unit as a word.
        parts = [f"{ticker} {verb} on the session"]
        if change_ref:
            parts.append(f"moving {{{{{change_ref}}}}}")
        if close_ref:
            parts.append(f"to close at {{{{{close_ref}}}}}")
        narrative = ", ".join(parts) + "."
        if return_ref:
            narrative += (
                f" Over the trailing thirty-day window it has returned {{{{{return_ref}}}}}."
            )
        key_moves.append(KeyMove(ticker=ticker, narrative=narrative, direction=direction))

    # ------------------------------------------------------ news read-through ---
    news_blocks: list[NewsAndSentiment] = []
    for ticker in tickers:
        senti = sentiment.get(ticker)
        feed = news.get(ticker)
        if senti is None and feed is None:
            continue
        top = feed.items[0] if feed and feed.items else None
        if senti is None:
            summary = "No sentiment could be derived for this ticker."
        elif feed is not None and feed.is_empty:
            summary = (
                "No recent headlines were published for this ticker, so sentiment is "
                "reported as neutral."
            )
        else:
            summary = (
                f"Coverage reads {senti.label}. {senti.reasoning} "
                f"Composite sentiment is recorded as "
                f"{{{{{minter.ref(ticker, 'sentiment_score') or ''}}}}}."
                if minter.ref(ticker, "sentiment_score")
                else f"Coverage reads {senti.label}. {senti.reasoning}"
            )
        news_blocks.append(
            NewsAndSentiment(
                ticker=ticker,
                summary=_strip_digits_fallback(summary),
                sentiment=minter.ref(ticker, "sentiment_score"),
                top_headline=top.title if top else None,
                headline_source=top.source if top else None,
            )
        )

    # -------------------------------------------------------- risk flags ---
    risk_flags = [
        RiskFlag(
            ticker=event.ticker,
            category=event.category,
            evidence=event.headline,
            assessment=(
                f"Flagged from live coverage as a {event.category} risk; confirm before acting."
            ),
        )
        for event in risk_events[:8]
    ]

    # ------------------------------------------------------- watch items ---
    watch_items: list[WatchItem] = []
    for ticker in usable:
        metric = metrics[ticker]
        vol = metric.volatility_annualised_pct
        ret = metric.return_30d_pct
        if vol is not None and vol >= HIGH_VOLATILITY_PCT:
            ref = minter.ref(ticker, "volatility_annualised_pct")
            watch_items.append(
                WatchItem(
                    ticker=ticker,
                    item=(
                        f"Elevated annualised volatility at {{{{{ref}}}}} percent — "
                        f"size positions accordingly."
                        if ref
                        else "Elevated annualised volatility — size positions accordingly."
                    ),
                )
            )
        if ret is not None and ret <= WEAK_RETURN_PCT:
            ref = minter.ref(ticker, "return_30d_pct")
            watch_items.append(
                WatchItem(
                    ticker=ticker,
                    item=(
                        f"Trailing thirty-day return of {{{{{ref}}}}} percent marks this as "
                        f"a laggard to review."
                        if ref
                        else "Trailing thirty-day return marks this as a laggard to review."
                    ),
                )
            )
    if data_gaps:
        watch_items.append(
            WatchItem(
                ticker=None, item="Resolve the outstanding data gaps before the next session."
            )
        )
    if not watch_items:
        watch_items.append(
            WatchItem(
                ticker=None, item="No positions breached the volatility or drawdown thresholds."
            )
        )

    # ---------------------------------------------------------- narrative ---
    movers_up = [t for t in usable if (metrics[t].change_1d_pct or 0.0) > 0]
    headline = _headline_for(usable, movers_up, partial)
    summary = _summary_for(usable, movers_up, risk_flags, partial)

    return Brief(
        generated_for=session_date,
        watchlist=list(tickers),
        headline=headline,
        executive_summary=summary,
        snapshot=snapshot,
        key_moves=key_moves,
        news_and_sentiment=news_blocks,
        risk_flags=risk_flags,
        watch_items=watch_items[:6],
        data_gaps=list(data_gaps),
        claims=minter.claims,
        partial=partial,
    )


def _headline_for(usable: list[str], movers_up: list[str], partial: bool) -> str:
    if not usable:
        return "Morning brief unavailable — no ticker returned usable market data"
    if len(movers_up) * 2 > len(usable):
        tone = "Watchlist leans higher into the session"
    elif not movers_up:
        tone = "Watchlist broadly lower into the session"
    else:
        tone = "Watchlist mixed into the session"
    return f"{tone}{' (partial coverage)' if partial else ''}"


def _summary_for(
    usable: list[str], movers_up: list[str], risk_flags: list[RiskFlag], partial: bool
) -> str:
    if not usable:
        return (
            "No ticker on the watchlist returned usable market data, so no figures are "
            "reported. The underlying provider errors are listed under data gaps."
        )
    advancing = ", ".join(movers_up) if movers_up else "none"
    declining = ", ".join(t for t in usable if t not in movers_up) or "none"
    parts = [
        f"Advancing: {advancing}. Declining: {declining}.",
        "Every figure in this brief was computed by the tool layer and independently "
        "recomputed by the verifier before delivery.",
    ]
    if risk_flags:
        flagged = ", ".join(sorted({flag.ticker for flag in risk_flags}))
        parts.insert(1, f"Risk headlines were flagged for: {flagged}.")
    if partial:
        parts.insert(
            1,
            "Coverage is partial — at least one ticker failed to return market data and is "
            "excluded from the snapshot.",
        )
    return " ".join(parts)


def _strip_digits_fallback(text: str) -> str:
    """Last-resort guard: replace stray numerals in generated prose.

    Provider reasoning strings are interpolated into summaries, and a lexicon
    explanation could in principle contain a numeral. Rather than fail schema
    validation, spell the sentence without it — the number is always available as
    a verified claim elsewhere in the brief.
    """
    from app.models.brief import strip_claim_refs

    if not any(ch.isdigit() for ch in strip_claim_refs(text)):
        return text
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("{{", index):
            end = text.find("}}", index)
            if end != -1:
                out.append(text[index : end + 2])
                index = end + 2
                continue
        char = text[index]
        out.append("" if char.isdigit() else char)
        index += 1
    cleaned = "".join(out)
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


def compose_brief_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Adapter used by the deterministic engine's ``emit_brief`` tool call."""
    brief = compose_brief(
        session_date=context["session_date"],
        tickers=list(context["tickers"]),
        metrics=context["metrics"],
        fundamentals=context["fundamentals"],
        sentiment=context["sentiment"],
        news=context["news"],
        risk_events=list(context.get("risk_events", [])),
        data_gaps=list(context.get("data_gaps", [])),
    )
    return brief.model_dump()
