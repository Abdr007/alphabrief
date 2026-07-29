"""Run orchestration: start a run, pause at the human gate, resume on decision.

The graph is invoked twice per brief:

1. ``ainvoke(initial_state)`` runs supervisor → workers → writer → verify → gate,
   where :func:`langgraph.types.interrupt` suspends it and checkpoints state.
2. ``ainvoke(Command(resume=decision))`` continues from that checkpoint through
   delivery.

Between the two the process could restart entirely — that is the point of the
checkpointer, and why approval is a separate authenticated HTTP request rather
than a callback held in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.core.budget import BudgetGuard
from app.core.claude import build_engine
from app.core.events import EventBus, EventKind, get_event_bus
from app.core.settings import Settings, get_settings
from app.core.tracing import Tracer, get_tracer
from app.graph.build import build_graph
from app.graph.state import RunState, initial_state, is_partial
from app.mcp_server.client import McpToolClient, ToolCallRecord
from app.models.brief import Brief
from app.models.run import HumanDecision, RunMode, RunStatus, TokenSpend
from app.services.render import render_markdown
from app.services.repository import Repository, get_repository
from app.services.serde import build_checkpoint_serde

logger = logging.getLogger(__name__)

#: Symbol injected by `demo_fault` mode. Verified to return no provider data.
DEMO_FAULT_TICKER = "ZZZZQQQQ"


class RunNotFoundError(RuntimeError):
    """No such run, or the run is not awaiting a decision."""


class RunNotResumableError(RuntimeError):
    """The run is awaiting a decision but its checkpoint is gone.

    Raised rather than silently re-invoking the graph from scratch: a
    ``Command(resume=...)`` against an unknown thread does not fail loudly, it
    starts a brand-new execution whose state never reaches the gate.
    """


#: Reason recorded against runs abandoned by a previous process.
ORPHAN_REASON = (
    "abandoned by a server restart — no checkpoint survived, so this run could "
    "not be resumed and was closed to release its watchlist"
)


def watchlist_key_for(tickers: list[str]) -> str:
    """Canonical key for the active-run constraint."""
    return ",".join(sorted({t.strip().upper() for t in tickers if t.strip()}))


class RunService:
    """Owns the compiled graph, the checkpointer and the background run tasks."""

    def __init__(
        self,
        settings: Settings | None = None,
        repository: Repository | None = None,
        bus: EventBus | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or get_repository()
        self.bus = bus or get_event_bus()
        self.tracer = tracer or get_tracer()
        #: True only when checkpoints outlive the process (Postgres).
        self._durable_checkpoints = False
        #: Guards the one-time async upgrade performed by `ensure_ready`.
        self._checkpointer_ready = False
        #: Held so shutdown can close it; only set on the Postgres path.
        self._pg_connection: Any | None = None
        self._checkpointer = self._build_checkpointer()
        self.graph: CompiledStateGraph[Any, Any, Any, Any] = build_graph(self._checkpointer)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started_at: dict[str, float] = {}

    # ------------------------------------------------------------ checkpointer ---
    def _build_checkpointer(self) -> Any:
        """The in-process saver the service always starts with.

        Postgres cannot be set up here: the async saver needs an awaited
        connection and an awaited ``setup()``, and ``__init__`` cannot await.
        :meth:`ensure_ready` performs that upgrade during application startup.

        Every saver is given a strict serializer allowlisted to AlphaBrief's own
        models (see :mod:`app.services.serde`), so checkpoint bytes can never
        rehydrate an arbitrary type.
        """
        serde = build_checkpoint_serde()
        from langgraph.checkpoint.memory import InMemorySaver

        self._durable_checkpoints = False
        logger.info("LangGraph checkpointer: InMemorySaver (paused runs end at shutdown)")
        return InMemorySaver(serde=serde)

    async def ensure_ready(self) -> None:
        """Upgrade to a durable checkpointer when Postgres is configured.

        Called once during application startup, before reconciliation.

        The **async** saver is mandatory here, not a preference: the graph is
        driven by ``ainvoke``, and langgraph's synchronous ``PostgresSaver``
        inherits ``aget_tuple`` from the base class, where it raises
        ``NotImplementedError``. Pairing it with an async graph fails every run
        the moment the loop opens.

        Falls back to the in-memory saver on any connection problem: a database
        outage should cost durability, not the ability to produce briefs.
        """
        if self._checkpointer_ready:
            return
        self._checkpointer_ready = True

        url = self.settings.database_url
        if not url.startswith(("postgres://", "postgresql://", "postgresql+")):
            return

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            from app.services.repository import normalise_database_url

            dsn = normalise_database_url(url).replace("postgresql+psycopg://", "postgresql://")
            # prepare_threshold=0 because the pooled endpoint multiplexes
            # sessions; server-side prepared statements do not survive that.
            connection = await AsyncConnection.connect(
                dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
            )
            saver = AsyncPostgresSaver(connection, serde=build_checkpoint_serde())
            await saver.setup()
        except Exception:
            logger.warning(
                "Postgres checkpointer unavailable; staying on the in-memory saver",
                exc_info=True,
            )
            return

        self._pg_connection = connection
        self._checkpointer = saver
        self.graph = build_graph(saver)
        self._durable_checkpoints = True
        logger.info("LangGraph checkpointer: AsyncPostgresSaver (durable)")

    # ------------------------------------------------------------ reconcile ---
    async def _has_checkpoint(self, run_id: str) -> bool:
        """Whether the checkpointer still holds a resumable thread for this run.

        Uses the async interface because that is the one the graph itself uses —
        checking through a path the saver does not actually implement would make
        every run look unresumable.
        """
        config = {"configurable": {"thread_id": run_id}}
        try:
            return await self._checkpointer.aget_tuple(config) is not None
        except Exception:
            logger.warning("checkpoint lookup failed for %s", run_id, exc_info=True)
            return False

    async def reconcile_orphans(self) -> int:
        """Close runs abandoned by a previous process, releasing their watchlists.

        Every active status occupies its watchlist's single active-run slot, and
        no code path moves an abandoned row out of that set — so without this a
        crash mid-run makes the watchlist permanently un-runnable. A run is an
        orphan when this process is not executing it and it cannot be resumed:

        * ``QUEUED``/``RUNNING`` — nothing resumes mid-graph work, so an inherited
          row is always an orphan regardless of checkpoint durability.
        * ``AWAITING_APPROVAL``/``HUMAN_REVIEW`` — genuinely resumable, but only
          while a checkpoint survives. With Postgres it does and the run is kept.
        """
        try:
            active = await self.repository.active_runs()
        except Exception:
            logger.warning("orphan reconciliation skipped: could not list runs", exc_info=True)
            return 0

        closed = 0
        for record in active:
            run_id = record["id"]
            if run_id in self._tasks:
                continue
            status = record["status"]
            resumable = status in (RunStatus.AWAITING_APPROVAL, RunStatus.HUMAN_REVIEW)
            if resumable and self._durable_checkpoints and await self._has_checkpoint(run_id):
                logger.info("run %s kept: durable checkpoint intact at the gate", run_id)
                continue
            await self.repository.finish_run(
                run_id,
                status=RunStatus.FAILED,
                abort_reason=ORPHAN_REASON,
            )
            closed += 1
            logger.warning("run %s closed as orphaned (was %s)", run_id, status)

        if closed:
            logger.warning("reconciled %d orphaned run(s) from a previous process", closed)
        return closed

    # -------------------------------------------------------------------- api ---
    async def start_run(
        self,
        *,
        tickers: list[str],
        mode: RunMode = RunMode.STANDARD,
        trigger: str = "ui",
    ) -> str:
        """Register a run and kick off graph execution in the background."""
        cleaned = [t.strip().upper() for t in tickers if t.strip()]
        if mode == RunMode.DEMO_FAULT and DEMO_FAULT_TICKER not in cleaned:
            cleaned.append(DEMO_FAULT_TICKER)

        run_id = f"run_{uuid.uuid4().hex[:20]}"
        key = watchlist_key_for(cleaned)

        await self.repository.create_run(
            run_id=run_id,
            watchlist_key=key,
            tickers=cleaned,
            mode=str(mode),
            engine=self.settings.resolved_engine,
            trigger=trigger,
        )

        await self.bus.publish(
            run_id,
            EventKind.RUN_STARTED,
            f"Run started for {', '.join(cleaned)}",
            {
                "tickers": cleaned,
                "mode": str(mode),
                "engine": self.settings.resolved_engine,
                "trigger": trigger,
            },
        )

        self._started_at[run_id] = time.perf_counter()
        task = asyncio.create_task(self._execute(run_id, cleaned, key, mode))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return run_id

    async def submit_decision(self, run_id: str, decision: HumanDecision) -> dict[str, Any]:
        """Resume a paused graph with the reviewer's decision."""
        record = await self.repository.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"run '{run_id}' not found")
        if record["status"] not in (RunStatus.AWAITING_APPROVAL, RunStatus.HUMAN_REVIEW):
            raise RunNotFoundError(f"run '{run_id}' is {record['status']}, not awaiting a decision")

        # A resume against a missing thread does not raise — LangGraph would start
        # a fresh execution that never reaches the gate, and the run would land in
        # FAILED with no explanation. Refuse explicitly instead.
        if not await self._has_checkpoint(run_id):
            await self.repository.finish_run(
                run_id,
                status=RunStatus.FAILED,
                abort_reason=ORPHAN_REASON,
            )
            raise RunNotResumableError(
                f"run '{run_id}' can no longer be approved: the server restarted and its "
                "checkpoint was lost. The brief is still in the archive. Configure "
                "DATABASE_URL with Postgres to make paused runs survive a restart."
            )

        await self.repository.save_approval(
            run_id=run_id,
            action=decision.action,
            reviewer=decision.reviewer,
            note=decision.note,
        )

        budget = BudgetGuard(self.settings)
        async with McpToolClient(settings=self.settings) as mcp:
            ctx = self._context(run_id, mcp, budget)
            final = await self.graph.ainvoke(
                Command(resume=decision.model_dump()),
                ctx.to_config(),
                context=ctx,
            )

        await self._finalise(run_id, final)
        return {"run_id": run_id, "status": final.get("status"), "action": decision.action}

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return await self.repository.get_run(run_id)

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self.repository.list_runs(limit)

    async def gate_payload(self, run_id: str) -> dict[str, Any] | None:
        """The approval-gate view for a paused run."""
        archived = await self.repository.latest_brief(run_id)
        if archived is None:
            return None
        record = await self.repository.get_run(run_id)
        return {
            "run_id": run_id,
            "status": record["status"] if record else None,
            "verified": archived["verified"],
            "requires_review": not archived["verified"],
            "brief": archived["brief"],
            "verification": archived["verification"],
            "markdown": archived["markdown"],
        }

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tracer.flush()
        if self._pg_connection is not None:
            with contextlib.suppress(Exception):
                await self._pg_connection.close()
            self._pg_connection = None

    # --------------------------------------------------------------- internal ---
    def _context(self, run_id: str, mcp: McpToolClient, budget: BudgetGuard) -> Any:
        from app.graph.context import RunContext

        ctx = RunContext(
            run_id=run_id,
            settings=self.settings,
            engine=build_engine(self.settings),
            mcp=mcp,
            bus=self.bus,
            tracer=self.tracer,
            budget=budget,
        )

        async def _emit_tool_call(record: ToolCallRecord) -> None:
            await ctx.emit(
                EventKind.MCP_TOOL_CALL,
                f"{record.tool} · {record.duration_ms:.0f}ms",
                record.to_dict(),
            )

        mcp.emitter = _emit_tool_call
        return ctx

    async def _execute(
        self, run_id: str, tickers: list[str], watchlist_key: str, mode: RunMode
    ) -> None:
        """Background task: run the graph up to the human gate."""
        budget = BudgetGuard(self.settings)
        session_date = datetime.now(UTC).date().isoformat()
        state = initial_state(
            run_id=run_id,
            watchlist_key=watchlist_key,
            tickers=tickers,
            session_date=session_date,
            mode=mode,
            days=self.settings.price_history_days,
            news_limit=self.settings.news_limit,
            max_regenerations=self.settings.max_regenerations,
        )

        try:
            async with McpToolClient(settings=self.settings) as mcp:
                ctx = self._context(run_id, mcp, budget)
                result = await asyncio.wait_for(
                    self.graph.ainvoke(state, ctx.to_config(), context=ctx),
                    timeout=self.settings.run_timeout_seconds,
                )
        except TimeoutError:
            await self._fail(run_id, "run exceeded its wall-clock timeout")
            return
        except Exception as exc:
            logger.exception("run %s crashed", run_id)
            await self._fail(run_id, f"{type(exc).__name__}: {exc}")
            return

        await self._after_first_pass(run_id, result)

    async def _after_first_pass(self, run_id: str, result: dict[str, Any]) -> None:
        """Persist the brief and mark the run as awaiting a human decision."""
        status = result.get("status")
        interrupted = "__interrupt__" in result

        if not interrupted:
            # Aborted before the gate (budget or iteration cap), or failed.
            terminal = (
                status
                if status
                in (
                    RunStatus.BUDGET_ABORT,
                    RunStatus.ITERATION_ABORT,
                )
                else RunStatus.FAILED
            )
            await self._persist_metrics(run_id, result)
            await self.repository.finish_run(
                run_id,
                str(terminal),
                abort_reason=result.get("abort_reason"),
            )
            await self.bus.publish(
                run_id,
                EventKind.RUN_FAILED,
                f"Run ended with status {terminal}",
                {"status": str(terminal), "reason": result.get("abort_reason")},
            )
            await self._archive_events(run_id)
            return

        brief = result.get("brief")
        report = result.get("verification") or {}
        verified = bool(report.get("ok"))
        gate_status = RunStatus.AWAITING_APPROVAL if verified else RunStatus.HUMAN_REVIEW

        if isinstance(brief, Brief):
            await self.repository.save_brief(
                run_id=run_id,
                generated_for=brief.generated_for,
                headline=brief.headline,
                partial=brief.partial,
                verified=verified,
                claims_total=int(report.get("checked_claims", 0)),
                claims_matched=int(report.get("matched", 0)),
                brief_json=brief.model_dump(),
                verification_json=report,
                markdown=render_markdown(brief),
            )

        await self._persist_metrics(run_id, result, status=str(gate_status), verified=verified)
        await self.bus.publish(
            run_id,
            EventKind.GATE_AWAITING,
            "Awaiting human approval"
            if verified
            else "Verification failed — flagged for human review",
            {
                "verified": verified,
                "status": str(gate_status),
                "claims_matched": report.get("matched"),
                "claims_total": report.get("checked_claims"),
                "partial": bool(brief.partial) if isinstance(brief, Brief) else True,
            },
        )

    async def _finalise(self, run_id: str, result: dict[str, Any]) -> None:
        """Persist terminal state after the gate resumes."""
        status = str(result.get("status") or RunStatus.FAILED)
        brief = result.get("brief")
        report = result.get("verification") or {}

        if isinstance(brief, Brief):
            await self.repository.save_brief(
                run_id=run_id,
                generated_for=brief.generated_for,
                headline=brief.headline,
                partial=brief.partial,
                verified=bool(report.get("ok")),
                claims_total=int(report.get("checked_claims", 0)),
                claims_matched=int(report.get("matched", 0)),
                brief_json=brief.model_dump(),
                verification_json=report,
                markdown=render_markdown(brief),
            )

        # record_latency=False: the reported latency is the machine time to
        # produce the brief, measured when it reached the gate. Everything after
        # that point is a human reading it.
        await self._persist_metrics(run_id, result, status=status, record_latency=False)
        await self.repository.finish_run(run_id, status)
        await self.bus.publish(
            run_id,
            EventKind.RUN_COMPLETED,
            f"Run {status.lower()}",
            {
                "status": status,
                "delivery": result.get("delivery"),
                "cost_usd": _spend(result).cost_usd,
            },
        )
        await self._archive_events(run_id)

    async def _elapsed_ms(self, run_id: str) -> float:
        """Milliseconds of machine work since the run was triggered.

        Prefers the in-process monotonic clock, which is immune to wall-clock
        adjustments. Falls back to the persisted `created_at`, because
        `perf_counter` is process-relative and therefore meaningless after a
        restart — and now that paused runs legitimately survive restarts, that
        fallback is a normal path rather than an edge case.
        """
        started = self._started_at.get(run_id)
        if started is not None:
            return (time.perf_counter() - started) * 1000
        record = await self.repository.get_run(run_id)
        created_raw = record.get("created_at") if record else None
        if not isinstance(created_raw, str):
            return 0.0
        try:
            created = datetime.fromisoformat(created_raw)
        except ValueError:
            return 0.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - created).total_seconds() * 1000)

    async def _persist_metrics(
        self,
        run_id: str,
        result: dict[str, Any],
        status: str | None = None,
        verified: bool | None = None,
        *,
        record_latency: bool = True,
    ) -> None:
        """Write run metrics.

        `record_latency=False` preserves the measurement taken when the brief
        reached the gate. Recomputing after approval would fold the reviewer's
        deliberation time into a figure that is reported as the system's
        latency — a five-minute coffee break would read as a five-minute brief.
        """
        spend = _spend(result)
        fields: dict[str, Any] = {
            "iterations": int(result.get("iterations", 0)),
            "model_calls": spend.calls,
            "input_tokens": spend.input_tokens,
            "output_tokens": spend.output_tokens,
            "cost_usd": spend.cost_usd,
            "tool_calls": len(result.get("tool_calls", [])),
            "partial": is_partial(_as_state(result)),
            "error_count": len(result.get("errors", [])),
        }
        if record_latency:
            fields["latency_ms"] = await self._elapsed_ms(run_id)
        if status is not None:
            fields["status"] = status
        if verified is not None:
            fields["verified"] = verified
        await self.repository.update_run(run_id, **fields)

    async def _fail(self, run_id: str, reason: str) -> None:
        # A crashed run still consumed real time; leaving latency at 0 would
        # understate the cost of a failure in the archive.
        await self.repository.finish_run(
            run_id,
            str(RunStatus.FAILED),
            abort_reason=reason,
            latency_ms=await self._elapsed_ms(run_id),
        )
        await self.bus.publish(run_id, EventKind.RUN_FAILED, reason, {"reason": reason})
        await self._archive_events(run_id)

    async def _archive_events(self, run_id: str) -> None:
        try:
            events = await self.bus.history(run_id)
            await self.repository.save_events(events)
        except Exception:
            logger.warning("failed to archive events for %s", run_id, exc_info=True)


def _spend(result: dict[str, Any]) -> TokenSpend:
    value = result.get("token_spend")
    if isinstance(value, TokenSpend):
        return value
    if isinstance(value, dict):
        return TokenSpend.model_validate(value)
    return TokenSpend()


def _as_state(result: dict[str, Any]) -> RunState:
    """View a graph result as RunState, dropping LangGraph's internal keys."""
    cleaned = {key: value for key, value in result.items() if not key.startswith("__")}
    return cast(RunState, cleaned)


_service: RunService | None = None


def get_run_service() -> RunService:
    """Process-wide run service singleton."""
    global _service  # noqa: PLW0603 - deliberate process-wide singleton
    if _service is None:
        _service = RunService()
    return _service


def reset_run_service() -> None:
    """Test hook."""
    global _service  # noqa: PLW0603 - deliberate process-wide singleton
    _service = None
