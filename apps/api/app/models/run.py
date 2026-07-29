"""Run-level contracts: status, errors, spend, human decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """Lifecycle of a single brief."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    BUDGET_ABORT = "BUDGET_ABORT"
    ITERATION_ABORT = "ITERATION_ABORT"


#: Statuses that hold a slot in the "one active run per watchlist" constraint.
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL, RunStatus.HUMAN_REVIEW}
)

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        RunStatus.APPROVED,
        RunStatus.REJECTED,
        RunStatus.DELIVERED,
        RunStatus.FAILED,
        RunStatus.BUDGET_ABORT,
        RunStatus.ITERATION_ABORT,
    }
)


class RunMode(StrEnum):
    """Standard run, or the interviewer-facing fault-injection demo."""

    STANDARD = "standard"
    #: Injects a fake ticker so the graceful-degradation path is visible live.
    DEMO_FAULT = "demo_fault"
    #: Forces the writer to emit a wrong number so the verifier goes red on screen.
    DEMO_MISMATCH = "demo_mismatch"


Severity = Literal["warning", "error"]
Stage = Literal["supervisor", "data_agent", "news_agent", "writer", "verify", "gate", "deliver"]


class RunError(BaseModel):
    """A per-ticker or per-stage failure that the run degraded around."""

    model_config = ConfigDict(frozen=True)

    stage: Stage
    message: str
    ticker: str | None = None
    severity: Severity = "error"
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def key(self) -> tuple[str, str | None, str]:
        return (self.stage, self.ticker, self.message)


class TokenSpend(BaseModel):
    """Cumulative model spend for a run."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def plus(self, other: TokenSpend) -> TokenSpend:
        return TokenSpend(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 8),
            calls=self.calls + other.calls,
        )


DecisionAction = Literal["approve", "reject", "edit"]


class HumanDecision(BaseModel):
    """The human gate's verdict, resumed into the paused graph."""

    model_config = ConfigDict(frozen=True)

    action: DecisionAction
    reviewer: str = "analyst"
    note: str | None = None
    #: Only honoured for `edit`; narrative-only, never numbers.
    edited_headline: str | None = None
    edited_summary: str | None = None
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
