"""Request/response contracts for the HTTP surface.

Validation is strict and total: unknown fields are rejected, tickers are
normalised and bounded, and free-text is length-capped. Nothing reaches the graph
that has not already been shaped here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.settings import get_settings
from app.mcp_server.providers import TICKER_MAX_LEN
from app.models.run import RunMode

MAX_NOTE_CHARS = 1000
MAX_EDIT_CHARS = 2000
#: Raw submitted ticker entries may exceed the watchlist size (duplicates are
#: collapsed), but not without bound.
MAX_RAW_TICKER_MULTIPLIER = 4


class StrictRequest(BaseModel):
    """Base: reject unknown fields rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class CreateRunRequest(StrictRequest):
    """POST /v1/runs"""

    tickers: list[str] | None = Field(
        default=None,
        description="Watchlist to research. Defaults to the configured watchlist.",
    )
    mode: RunMode = RunMode.STANDARD

    @field_validator("tickers")
    @classmethod
    def _validate_tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        settings = get_settings()
        # Bound the raw list before any per-element work, so a huge array is
        # rejected rather than normalised. Duplicates are then collapsed, and the
        # deduplicated watchlist is what the size limit applies to.
        if len(value) > settings.max_watchlist_size * MAX_RAW_TICKER_MULTIPLIER:
            raise ValueError(
                f"too many ticker entries submitted (limit "
                f"{settings.max_watchlist_size * MAX_RAW_TICKER_MULTIPLIER})"
            )
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            ticker = (raw or "").strip().upper()
            if not ticker:
                continue
            if len(ticker) > TICKER_MAX_LEN:
                raise ValueError(f"ticker '{ticker[:20]}' exceeds {TICKER_MAX_LEN} characters")
            if not all(ch.isalnum() or ch in {".", "-", "^"} for ch in ticker):
                raise ValueError(f"ticker '{ticker}' contains unsupported characters")
            if ticker in seen:
                continue
            seen.add(ticker)
            cleaned.append(ticker)
        if not cleaned:
            raise ValueError("at least one ticker is required")
        if len(cleaned) > settings.max_watchlist_size:
            raise ValueError(
                f"watchlist exceeds the maximum of {settings.max_watchlist_size} tickers"
            )
        return cleaned

    def resolved_tickers(self) -> list[str]:
        return self.tickers if self.tickers else get_settings().watchlist


class CreateRunResponse(BaseModel):
    run_id: str
    status: str
    tickers: list[str]
    mode: str
    engine: str
    stream_url: str


class DecisionRequest(StrictRequest):
    """POST /v1/runs/{run_id}/decision"""

    action: Literal["approve", "reject", "edit"]
    reviewer: Annotated[str, Field(max_length=128)] = "analyst"
    note: Annotated[str, Field(max_length=MAX_NOTE_CHARS)] | None = None
    edited_headline: Annotated[str, Field(max_length=MAX_EDIT_CHARS)] | None = None
    edited_summary: Annotated[str, Field(max_length=MAX_EDIT_CHARS)] | None = None


class DecisionResponse(BaseModel):
    run_id: str
    status: str
    action: str


class RunSummary(BaseModel):
    """One row of the archive."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    tickers: list[str]
    mode: str
    engine: str
    created_at: str
    iterations: int
    cost_usd: float
    latency_ms: float
    tool_calls: int
    partial: bool
    verified: bool
    headline: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    engine: str
    mcp_transport: str
    langfuse: bool
    smtp: bool
    database: str
    #: Whether a run paused at the human gate would survive a restart of this
    #: process. False means the checkpointer degraded to the in-memory saver.
    durable_checkpoints: bool
    watchlist: list[str]
    models: dict[str, str]


class ToolDoc(BaseModel):
    """Discoverable MCP tool documentation."""

    name: str
    description: str
    input_schema: dict[str, Any]
