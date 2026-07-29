"""The Pydantic-enforced brief — where hallucinated numbers become impossible.

The writer agent *assembles*; it never computes. That guarantee is enforced by
the schema itself, not by asking nicely in a prompt:

1. **Every number is a claim.** Narrative prose may not contain a bare digit. A
   figure is written as a placeholder token ``{{c3}}`` that references a
   :class:`NumericClaim`. Pydantic rejects the brief outright if prose contains a
   digit outside a claim reference.
2. **Every claim is recomputed.** The deterministic verifier recalculates each
   claim's value from the raw price bars in state and compares to the cent.
3. **Every quotation is matched.** Fields holding third-party text (headlines)
   must appear verbatim in the news actually retrieved for that ticker.

So the writer can only choose *which* verified figure to cite and *how to phrase*
the surrounding sentence. It cannot type a number into the brief at all.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.market import METRIC_UNITS, MetricUnit

#: A reference to a NumericClaim inside narrative prose.
CLAIM_REF_PATTERN = re.compile(r"\{\{(c\d+)\}\}")
CLAIM_ID_PATTERN = re.compile(r"^c\d+$")

ClaimId = Annotated[str, Field(pattern=r"^c\d+$")]


def extract_claim_refs(text: str) -> list[str]:
    """All claim ids referenced by `text`, in order of appearance."""
    return CLAIM_REF_PATTERN.findall(text or "")


def strip_claim_refs(text: str) -> str:
    """`text` with every ``{{cN}}`` token removed."""
    return CLAIM_REF_PATTERN.sub(" ", text or "")


def validate_narrative(text: str, field_name: str) -> str:
    """Reject prose containing a numeral outside a claim reference."""
    residue = strip_claim_refs(text)
    offenders = sorted({ch for ch in residue if ch.isdigit()})
    if offenders:
        raise ValueError(
            f"{field_name} contains bare numerals {offenders!r}. Every figure must be a "
            f"verified claim reference such as {{{{c1}}}} — the writer performs no arithmetic."
        )
    return text


class NumericClaim(BaseModel):
    """One number in the brief, traceable to a tool-computed metric."""

    model_config = ConfigDict(extra="forbid")

    claim_id: ClaimId
    ticker: str
    metric: str
    value: float
    unit: MetricUnit

    @field_validator("metric")
    @classmethod
    def _known_metric(cls, value: str) -> str:
        if value not in METRIC_UNITS:
            raise ValueError(f"unknown metric '{value}'; must be one of {sorted(METRIC_UNITS)}")
        return value

    @field_validator("ticker")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


RowStatus = Literal["ok", "partial", "unavailable"]


class SnapshotRow(BaseModel):
    """One row of the snapshot table. All cells are claim references."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    company: str | None = None
    status: RowStatus = "ok"
    last_close: ClaimId | None = None
    change_1d: ClaimId | None = None
    return_30d: ClaimId | None = None
    volatility: ClaimId | None = None
    max_drawdown: ClaimId | None = None
    pe_ratio: ClaimId | None = None
    sentiment: ClaimId | None = None
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note_narrative(cls, value: str | None) -> str | None:
        return validate_narrative(value, "SnapshotRow.note") if value else value

    def claim_ids(self) -> list[str]:
        return [
            claim
            for claim in (
                self.last_close,
                self.change_1d,
                self.return_30d,
                self.volatility,
                self.max_drawdown,
                self.pe_ratio,
                self.sentiment,
            )
            if claim
        ]


class KeyMove(BaseModel):
    """A notable movement, described in prose with claim references."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    narrative: str
    direction: Literal["up", "down", "flat"]

    @field_validator("narrative")
    @classmethod
    def _narrative(cls, value: str) -> str:
        return validate_narrative(value, "KeyMove.narrative")


class NewsAndSentiment(BaseModel):
    """Per-ticker news read-through."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    summary: str
    sentiment: ClaimId | None = None
    #: Must appear verbatim in the headlines retrieved for this ticker.
    top_headline: str | None = None
    headline_source: str | None = None

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        return validate_narrative(value, "NewsAndSentiment.summary")


class RiskFlag(BaseModel):
    """A flagged risk. `evidence` is a verbatim retrieved headline."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    category: str
    #: Verbatim third-party text; matched against retrieved headlines.
    evidence: str
    assessment: str

    @field_validator("assessment")
    @classmethod
    def _assessment(cls, value: str) -> str:
        return validate_narrative(value, "RiskFlag.assessment")


class WatchItem(BaseModel):
    """Something to watch next session."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = None
    item: str

    @field_validator("item")
    @classmethod
    def _item(cls, value: str) -> str:
        return validate_narrative(value, "WatchItem.item")


class Brief(BaseModel):
    """The complete morning brief."""

    model_config = ConfigDict(extra="forbid")

    generated_for: str = Field(description="Session date, ISO-8601 (YYYY-MM-DD).")
    watchlist: list[str]
    headline: str
    executive_summary: str
    snapshot: list[SnapshotRow] = Field(default_factory=list)
    key_moves: list[KeyMove] = Field(default_factory=list)
    news_and_sentiment: list[NewsAndSentiment] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    watch_items: list[WatchItem] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    claims: list[NumericClaim] = Field(default_factory=list)
    #: True when one or more tickers failed — the brief is explicitly partial.
    partial: bool = False

    @field_validator("headline", "executive_summary")
    @classmethod
    def _narrative_fields(cls, value: str) -> str:
        return validate_narrative(value, "Brief narrative")

    @field_validator("data_gaps")
    @classmethod
    def _gaps(cls, value: list[str]) -> list[str]:
        # Data gaps quote provider error strings, which may legitimately contain
        # numerals ("need at least 2 bars"), so they are exempt from the digit
        # rule — but they are matched against recorded run errors by the verifier.
        return value

    def claims_by_id(self) -> dict[str, NumericClaim]:
        return {claim.claim_id: claim for claim in self.claims}

    def referenced_claim_ids(self) -> set[str]:
        """Every claim id referenced anywhere in the brief."""
        referenced: set[str] = set()
        for row in self.snapshot:
            referenced.update(row.claim_ids())
        for move in self.key_moves:
            referenced.update(extract_claim_refs(move.narrative))
        for entry in self.news_and_sentiment:
            if entry.sentiment:
                referenced.add(entry.sentiment)
            referenced.update(extract_claim_refs(entry.summary))
        for flag in self.risk_flags:
            referenced.update(extract_claim_refs(flag.assessment))
        for item in self.watch_items:
            referenced.update(extract_claim_refs(item.item))
        referenced.update(extract_claim_refs(self.headline))
        referenced.update(extract_claim_refs(self.executive_summary))
        for row in self.snapshot:
            if row.note:
                referenced.update(extract_claim_refs(row.note))
        return referenced

    def quoted_texts(self) -> list[tuple[str, str, str]]:
        """(ticker, field, verbatim text) for every quoted third-party string."""
        quotes: list[tuple[str, str, str]] = []
        for entry in self.news_and_sentiment:
            if entry.top_headline:
                quotes.append((entry.ticker, "news_and_sentiment.top_headline", entry.top_headline))
        for flag in self.risk_flags:
            quotes.append((flag.ticker, "risk_flags.evidence", flag.evidence))
        return quotes
