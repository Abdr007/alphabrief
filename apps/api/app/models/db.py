"""SQLAlchemy tables for runs, briefs, approvals and archived telemetry.

Runs on Neon Postgres in production and SQLite locally; both back the same
schema, including the partial unique index that enforces **one active run per
watchlist at the database level** rather than in application code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.run import ACTIVE_STATUSES

#: SQL fragment listing the statuses that occupy the single active-run slot.
_ACTIVE_SQL = ", ".join(f"'{status}'" for status in sorted(ACTIVE_STATUSES))
ACTIVE_RUN_PREDICATE = text(f"status IN ({_ACTIVE_SQL})")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base."""


class RunRecord(Base):
    """One brief run, from trigger to terminal status."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    watchlist_key: Mapped[str] = mapped_column(String(255), index=True)
    tickers: Mapped[list[str]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(32), default="standard")
    status: Mapped[str] = mapped_column(String(32), index=True)
    engine: Mapped[str] = mapped_column(String(32), default="deterministic")
    trigger: Mapped[str] = mapped_column(String(32), default="ui")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    iterations: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)

    partial: Mapped[bool] = mapped_column(default=False)
    verified: Mapped[bool] = mapped_column(default=False)
    abort_reason: Mapped[str | None] = mapped_column(Text, default=None)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    briefs: Mapped[list[BriefRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[ApprovalRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One active run per watchlist — enforced by the database, not by a
        # read-check-write race in the API layer. Partial unique indexes are
        # supported by both PostgreSQL and SQLite.
        Index(
            "uq_runs_one_active_per_watchlist",
            "watchlist_key",
            unique=True,
            postgresql_where=ACTIVE_RUN_PREDICATE,
            sqlite_where=ACTIVE_RUN_PREDICATE,
        ),
        Index("ix_runs_created_at", "created_at"),
    )


class BriefRecord(Base):
    """The archived brief and its verification report."""

    __tablename__ = "briefs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    generated_for: Mapped[str] = mapped_column(String(16))
    headline: Mapped[str] = mapped_column(Text)
    partial: Mapped[bool] = mapped_column(default=False)
    verified: Mapped[bool] = mapped_column(default=False)
    claims_total: Mapped[int] = mapped_column(Integer, default=0)
    claims_matched: Mapped[int] = mapped_column(Integer, default=0)
    brief_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    markdown: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[RunRecord] = relationship(back_populates="briefs")


class ApprovalRecord(Base):
    """The human decision that released (or blocked) a brief."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))
    reviewer: Mapped[str] = mapped_column(String(128), default="analyst")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[RunRecord] = relationship(back_populates="approvals")


class EventRecord(Base):
    """Archived telemetry so a completed run can be replayed in the UI.

    ``(run_id, seq)`` is the natural key and therefore the *primary* key: archival
    is retried whenever a run reaches a terminal state, so an upsert on the
    natural key must be idempotent. With a surrogate id, ``session.merge()``
    would insert duplicates and trip the unique index instead.
    """

    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_run_events_run_id", "run_id"),)
