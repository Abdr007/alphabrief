"""Transactional persistence for runs, briefs, approvals and telemetry.

Synchronous SQLAlchemy executed on a worker thread. That keeps one driver
(`psycopg` for Neon, stdlib `sqlite3` locally) and one set of semantics, and
avoids an async-driver matrix for what is a handful of small writes per run.

Every write is a single committed transaction. The "one active run per
watchlist" rule is enforced by a database index, so a duplicate trigger raises
:class:`ActiveRunExistsError` from the driver rather than being lost to a race.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.events import RunEvent
from app.core.settings import Settings, get_settings
from app.models.db import ApprovalRecord, Base, BriefRecord, EventRecord, RunRecord
from app.models.run import ACTIVE_STATUSES, RunStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ActiveRunExistsError(RuntimeError):
    """A run is already active for this watchlist."""

    def __init__(self, watchlist_key: str) -> None:
        self.watchlist_key = watchlist_key
        super().__init__(
            f"a run is already active for watchlist '{watchlist_key}'; "
            "approve or reject it before starting another"
        )


def normalise_database_url(url: str) -> str:
    """Coerce common Postgres URL spellings onto the psycopg driver."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Repository:
    """Thin data-access layer over the run archive."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        url = normalise_database_url(self._settings.database_url)
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine: Engine = create_engine(
            url,
            echo=self._settings.db_echo,
            future=True,
            pool_pre_ping=not url.startswith("sqlite"),
            connect_args=connect_args,
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    # ------------------------------------------------------------- lifecycle ---
    def create_all(self) -> None:
        """Create tables and indexes if they do not exist."""
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        self._engine.dispose()

    async def create_all_async(self) -> None:
        await asyncio.to_thread(self.create_all)

    def _run(self, fn: Callable[[Session], T]) -> T:
        with self._session_factory() as session, session.begin():
            return fn(session)

    async def _run_async(self, fn: Callable[[Session], T]) -> T:
        return await asyncio.to_thread(self._run, fn)

    # ------------------------------------------------------------------ runs ---
    async def create_run(
        self,
        *,
        run_id: str,
        watchlist_key: str,
        tickers: Sequence[str],
        mode: str,
        engine: str,
        trigger: str,
    ) -> None:
        """Insert a new run, or raise if one is already active for the watchlist."""

        def _create(session: Session) -> None:
            session.add(
                RunRecord(
                    id=run_id,
                    watchlist_key=watchlist_key,
                    tickers=list(tickers),
                    mode=mode,
                    status=RunStatus.RUNNING,
                    engine=engine,
                    trigger=trigger,
                )
            )

        try:
            await self._run_async(_create)
        except IntegrityError as exc:
            raise ActiveRunExistsError(watchlist_key) from exc

    async def update_run(self, run_id: str, **fields: Any) -> None:
        """Patch mutable columns on a run."""

        def _update(session: Session) -> None:
            record = session.get(RunRecord, run_id)
            if record is None:
                logger.warning("update_run: run %s not found", run_id)
                return
            for key, value in fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = datetime.now(UTC)

        await self._run_async(_update)

    async def finish_run(self, run_id: str, status: str, **fields: Any) -> None:
        """Mark a run terminal, freeing the watchlist's active slot."""
        await self.update_run(run_id, status=status, finished_at=datetime.now(UTC), **fields)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        def _get(session: Session) -> dict[str, Any] | None:
            record = session.get(RunRecord, run_id)
            return _run_to_dict(record) if record else None

        return await self._run_async(_get)

    async def active_run_for(self, watchlist_key: str) -> dict[str, Any] | None:
        def _get(session: Session) -> dict[str, Any] | None:
            stmt = (
                select(RunRecord)
                .where(RunRecord.watchlist_key == watchlist_key)
                .where(RunRecord.status.in_(sorted(ACTIVE_STATUSES)))
                .limit(1)
            )
            record = session.execute(stmt).scalar_one_or_none()
            return _run_to_dict(record) if record else None

        return await self._run_async(_get)

    async def active_runs(self) -> list[dict[str, Any]]:
        """Every run currently holding an active-run slot, oldest first.

        Used at boot to reconcile runs abandoned by a previous process: each one
        occupies its watchlist's single active slot, and nothing else will ever
        move it to a terminal status.
        """

        def _list(session: Session) -> list[dict[str, Any]]:
            stmt = (
                select(RunRecord)
                .where(RunRecord.status.in_(sorted(ACTIVE_STATUSES)))
                .order_by(RunRecord.created_at)
            )
            return [_run_to_dict(record) for record in session.execute(stmt).scalars().all()]

        return await self._run_async(_list)

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """The archive page: newest runs with cost, latency and iteration count."""
        capped = max(1, min(limit, 200))

        def _list(session: Session) -> list[dict[str, Any]]:
            stmt = select(RunRecord).order_by(desc(RunRecord.created_at)).limit(capped)
            records = session.execute(stmt).scalars().all()
            out: list[dict[str, Any]] = []
            for record in records:
                payload = _run_to_dict(record)
                brief_stmt = (
                    select(BriefRecord)
                    .where(BriefRecord.run_id == record.id)
                    .order_by(desc(BriefRecord.created_at))
                    .limit(1)
                )
                brief = session.execute(brief_stmt).scalar_one_or_none()
                payload["headline"] = brief.headline if brief else None
                payload["brief_id"] = brief.id if brief else None
                out.append(payload)
            return out

        return await self._run_async(_list)

    # ---------------------------------------------------------------- briefs ---
    async def save_brief(
        self,
        *,
        run_id: str,
        generated_for: str,
        headline: str,
        partial: bool,
        verified: bool,
        claims_total: int,
        claims_matched: int,
        brief_json: dict[str, Any],
        verification_json: dict[str, Any],
        markdown: str,
    ) -> str:
        brief_id = f"brf_{uuid.uuid4().hex[:20]}"

        def _save(session: Session) -> None:
            session.add(
                BriefRecord(
                    id=brief_id,
                    run_id=run_id,
                    generated_for=generated_for,
                    headline=headline,
                    partial=partial,
                    verified=verified,
                    claims_total=claims_total,
                    claims_matched=claims_matched,
                    brief_json=brief_json,
                    verification_json=verification_json,
                    markdown=markdown,
                )
            )

        await self._run_async(_save)
        return brief_id

    async def latest_brief(self, run_id: str) -> dict[str, Any] | None:
        def _get(session: Session) -> dict[str, Any] | None:
            stmt = (
                select(BriefRecord)
                .where(BriefRecord.run_id == run_id)
                .order_by(desc(BriefRecord.created_at))
                .limit(1)
            )
            record = session.execute(stmt).scalar_one_or_none()
            if record is None:
                return None
            return {
                "id": record.id,
                "run_id": record.run_id,
                "generated_for": record.generated_for,
                "headline": record.headline,
                "partial": record.partial,
                "verified": record.verified,
                "claims_total": record.claims_total,
                "claims_matched": record.claims_matched,
                "brief": record.brief_json,
                "verification": record.verification_json,
                "markdown": record.markdown,
                "created_at": record.created_at.isoformat(),
            }

        return await self._run_async(_get)

    # ------------------------------------------------------------- approvals ---
    async def save_approval(
        self, *, run_id: str, action: str, reviewer: str, note: str | None
    ) -> str:
        approval_id = f"apr_{uuid.uuid4().hex[:20]}"

        def _save(session: Session) -> None:
            session.add(
                ApprovalRecord(
                    id=approval_id,
                    run_id=run_id,
                    action=action,
                    reviewer=reviewer,
                    note=note,
                )
            )

        await self._run_async(_save)
        return approval_id

    # ---------------------------------------------------------------- events ---
    async def save_events(self, events: Sequence[RunEvent]) -> None:
        """Archive telemetry so a finished run can be replayed."""
        if not events:
            return

        def _save(session: Session) -> None:
            for event in events:
                session.merge(
                    EventRecord(
                        run_id=event.run_id,
                        seq=event.seq,
                        ts=event.ts,
                        kind=str(event.kind),
                        message=event.message,
                        payload=event.payload,
                    )
                )

        await self._run_async(_save)

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        def _list(session: Session) -> list[dict[str, Any]]:
            stmt = select(EventRecord).where(EventRecord.run_id == run_id).order_by(EventRecord.seq)
            return [
                {
                    "run_id": row.run_id,
                    "seq": row.seq,
                    "ts": row.ts,
                    "kind": row.kind,
                    "message": row.message,
                    "payload": row.payload,
                }
                for row in session.execute(stmt).scalars().all()
            ]

        return await self._run_async(_list)


def _run_to_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "watchlist_key": record.watchlist_key,
        "tickers": list(record.tickers or []),
        "mode": record.mode,
        "status": record.status,
        "engine": record.engine,
        "trigger": record.trigger,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "iterations": record.iterations,
        "model_calls": record.model_calls,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cost_usd": round(record.cost_usd, 6),
        "latency_ms": round(record.latency_ms, 1),
        "tool_calls": record.tool_calls,
        "partial": record.partial,
        "verified": record.verified,
        "abort_reason": record.abort_reason,
        "error_count": record.error_count,
    }


_repository: Repository | None = None


def get_repository() -> Repository:
    """Process-wide repository singleton."""
    global _repository  # noqa: PLW0603 - deliberate process-wide singleton
    if _repository is None:
        _repository = Repository()
        _repository.create_all()
    return _repository


def reset_repository() -> None:
    """Test hook."""
    global _repository  # noqa: PLW0603 - deliberate process-wide singleton
    if _repository is not None:
        _repository.dispose()
    _repository = None
