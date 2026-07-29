"""Recovery from a process restart.

Every active status occupies its watchlist's single active-run slot (enforced by
a partial unique index), and no ordinary code path moves a run abandoned by a
dead process out of that set. Without reconciliation at boot, a crash mid-run
makes that watchlist permanently un-runnable.

The second hazard is quieter: ``Command(resume=...)`` against a thread the
checkpointer no longer holds does *not* raise. LangGraph starts a fresh
execution that never reaches the gate, so an approval click would destroy the
run instead of delivering it. Both are pinned here.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.settings import Settings
from app.models.run import HumanDecision, RunStatus
from app.services.repository import ActiveRunExistsError, Repository
from app.services.runner import RunNotResumableError, RunService

WATCHLIST = "AAPL,MSFT"
TICKERS = ["AAPL", "MSFT"]


@pytest.fixture
def repository(temp_database_url: str) -> Repository:
    repo = Repository(Settings(database_url=temp_database_url))
    repo.create_all()
    return repo


@pytest.fixture
def service(temp_database_url: str, repository: Repository) -> RunService:
    """A run service sharing the test database, as a restarted process would."""
    return RunService(
        settings=Settings(
            database_url=temp_database_url,
            llm_engine="deterministic",
            mcp_transport="inmemory",
            approval_token="test-approval-token",
        ),
        repository=repository,
    )


async def _seed(repository: Repository, run_id: str, status: str, key: str = WATCHLIST) -> None:
    await repository.create_run(
        run_id=run_id,
        watchlist_key=key,
        tickers=TICKERS,
        mode="standard",
        engine="deterministic",
        trigger="test",
    )
    await repository.update_run(run_id, status=status)


class TestOrphanReconciliation:
    @pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
    async def test_mid_graph_runs_are_always_orphans(
        self, service: RunService, repository: Repository, status: RunStatus
    ) -> None:
        """Nothing resumes mid-graph work, so an inherited row can only be closed."""
        await _seed(repository, "run_orphan", str(status))

        assert await service.reconcile_orphans() == 1

        record = await repository.get_run("run_orphan")
        assert record is not None
        assert record["status"] == RunStatus.FAILED
        assert record["abort_reason"]
        assert record["finished_at"] is not None

    async def test_the_watchlist_becomes_runnable_again(
        self, service: RunService, repository: Repository
    ) -> None:
        """The whole point: a crash must not cost the user that watchlist forever."""
        await _seed(repository, "run_orphan", str(RunStatus.RUNNING))
        with pytest.raises(ActiveRunExistsError):
            await _seed(repository, "run_blocked", str(RunStatus.QUEUED))

        await service.reconcile_orphans()

        assert await repository.active_run_for(WATCHLIST) is None
        await _seed(repository, "run_after", str(RunStatus.QUEUED))
        active = await repository.active_run_for(WATCHLIST)
        assert active is not None
        assert active["id"] == "run_after"

    async def test_a_paused_run_is_closed_when_checkpoints_are_volatile(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))
        assert service._durable_checkpoints is False

        assert await service.reconcile_orphans() == 1
        record = await repository.get_run("run_gate")
        assert record is not None
        assert record["status"] == RunStatus.FAILED

    async def test_a_paused_run_survives_when_the_checkpoint_is_durable(
        self, service: RunService, repository: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With Postgres the thread outlives the process, so the gate must be kept."""
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))
        monkeypatch.setattr(service, "_durable_checkpoints", True)

        async def _present(_run_id: str) -> bool:
            return True

        monkeypatch.setattr(service, "_has_checkpoint", _present)

        assert await service.reconcile_orphans() == 0
        record = await repository.get_run("run_gate")
        assert record is not None
        assert record["status"] == RunStatus.AWAITING_APPROVAL

    async def test_a_durable_run_whose_thread_vanished_is_still_closed(
        self, service: RunService, repository: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))
        monkeypatch.setattr(service, "_durable_checkpoints", True)

        async def _missing(_run_id: str) -> bool:
            return False

        monkeypatch.setattr(service, "_has_checkpoint", _missing)

        assert await service.reconcile_orphans() == 1

    async def test_runs_owned_by_this_process_are_left_alone(
        self, service: RunService, repository: Repository
    ) -> None:
        """Reconciliation must never reap a run that is genuinely still executing."""
        await _seed(repository, "run_live", str(RunStatus.RUNNING))
        started = asyncio.Event()

        async def _busy() -> None:
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(_busy())
        service._tasks["run_live"] = task
        await started.wait()
        try:
            assert await service.reconcile_orphans() == 0
            record = await repository.get_run("run_live")
            assert record is not None
            assert record["status"] == RunStatus.RUNNING
        finally:
            task.cancel()
            service._tasks.pop("run_live", None)

    async def test_terminal_runs_are_never_touched(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_done", str(RunStatus.DELIVERED))
        assert await service.reconcile_orphans() == 0
        record = await repository.get_run("run_done")
        assert record is not None
        assert record["status"] == RunStatus.DELIVERED
        assert record["abort_reason"] is None


class TestCheckpointerIsAsyncCapable:
    """The graph is driven by `ainvoke`, so the saver must implement the async API.

    langgraph's synchronous `PostgresSaver` inherits `aget_tuple` from
    `BaseCheckpointSaver`, where it raises `NotImplementedError`. Pairing it with
    an async graph failed every run the instant the Pregel loop opened. This was a
    real outage the first time a Postgres URL was configured, so the invariant is
    pinned rather than left to reviewer memory.
    """

    async def test_the_active_checkpointer_implements_aget_tuple(self, service: RunService) -> None:
        from langgraph.checkpoint.base import BaseCheckpointSaver

        saver = service._checkpointer
        assert type(saver).aget_tuple is not BaseCheckpointSaver.aget_tuple

    async def test_a_checkpoint_lookup_succeeds_rather_than_raising(
        self, service: RunService
    ) -> None:
        """`_has_checkpoint` must answer False, not blow up, for an unknown thread."""
        assert await service._has_checkpoint("run_never_existed") is False

    async def test_the_sync_postgres_saver_is_the_wrong_one(self) -> None:
        """Documents why `ensure_ready` awaits the async saver instead."""
        from langgraph.checkpoint.base import BaseCheckpointSaver
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        assert PostgresSaver.aget_tuple is BaseCheckpointSaver.aget_tuple
        assert AsyncPostgresSaver.aget_tuple is not BaseCheckpointSaver.aget_tuple

    async def test_a_sqlite_url_never_claims_durability(self, service: RunService) -> None:
        await service.ensure_ready()
        assert service._durable_checkpoints is False

    async def test_an_unreachable_postgres_degrades_instead_of_crashing(
        self, temp_database_url: str, repository: Repository
    ) -> None:
        """A database outage must cost durability, not the ability to run at all."""
        unreachable = RunService(
            settings=Settings(
                database_url="postgresql://u:p@127.0.0.1:1/nope",
                llm_engine="deterministic",
                mcp_transport="inmemory",
                approval_token="test-approval-token",
            ),
            repository=repository,
        )
        await unreachable.ensure_ready()
        assert unreachable._durable_checkpoints is False
        assert await unreachable._has_checkpoint("run_x") is False
        await unreachable.shutdown()


class TestLatencyIsHonest:
    """Reported latency is machine time to produce the brief — nothing else.

    `perf_counter` is process-relative, so once paused runs began surviving
    restarts every approved-after-restart run reported 0.0s. And because
    `_finalise` recomputed the figure *after* approval, a reviewer who took five
    minutes to read a brief turned it into a five-minute brief. Both would have
    quietly corrupted the number this project puts on a résumé.
    """

    async def test_elapsed_falls_back_to_the_persisted_timestamp(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_restarted", str(RunStatus.AWAITING_APPROVAL))
        assert "run_restarted" not in service._started_at
        assert await service._elapsed_ms("run_restarted") > 0.0

    async def test_elapsed_prefers_the_monotonic_clock_when_in_process(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_inproc", str(RunStatus.RUNNING))
        service._started_at["run_inproc"] = time.perf_counter()
        assert 0.0 <= await service._elapsed_ms("run_inproc") < 5_000

    async def test_an_unknown_run_reports_zero_rather_than_raising(
        self, service: RunService
    ) -> None:
        assert await service._elapsed_ms("run_never_existed") == 0.0

    async def test_human_deliberation_never_inflates_reported_latency(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_frozen", str(RunStatus.AWAITING_APPROVAL))
        service._started_at["run_frozen"] = time.perf_counter()
        await service._persist_metrics("run_frozen", {}, status=str(RunStatus.AWAITING_APPROVAL))
        record = await repository.get_run("run_frozen")
        assert record is not None
        at_gate = record["latency_ms"]

        # The reviewer goes for coffee, then approves.
        service._started_at["run_frozen"] = time.perf_counter() - 600.0
        await service._persist_metrics(
            "run_frozen", {}, status=str(RunStatus.DELIVERED), record_latency=False
        )
        record = await repository.get_run("run_frozen")
        assert record is not None
        assert record["latency_ms"] == at_gate

    async def test_a_crashed_run_records_the_time_it_burned(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_crash", str(RunStatus.RUNNING))
        service._started_at["run_crash"] = time.perf_counter() - 2.0
        await service._fail("run_crash", "provider exploded")
        record = await repository.get_run("run_crash")
        assert record is not None
        assert record["status"] == RunStatus.FAILED
        assert record["latency_ms"] >= 1_000


class TestSuiteNeverMails:
    async def test_smtp_is_disabled_for_the_whole_suite(self) -> None:
        """`Settings` reads ../../.env, where a working operator credential lives.

        The integration tests approve briefs, so without the conftest override
        every run would mail a real inbox and depend on a live SMTP server.
        """
        from app.core.settings import Settings

        assert Settings().smtp_enabled is False

    async def test_delivery_degrades_to_a_no_op_rather_than_failing(self) -> None:
        from app.core.settings import Settings
        from app.models.brief import Brief
        from app.services.email import send_brief

        brief = Brief(
            generated_for="2026-07-29",
            watchlist=TICKERS,
            headline="No figures here",
            executive_summary="A summary with no numerals at all.",
        )
        result = await send_brief(brief, Settings())
        assert result.sent is False
        assert result.channel == "none"
        assert result.recipients == []


class TestApprovalAfterRestart:
    async def test_approving_a_lost_run_is_refused_loudly(
        self, service: RunService, repository: Repository
    ) -> None:
        """A silent re-run would mark the brief FAILED with no explanation."""
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))

        with pytest.raises(RunNotResumableError) as caught:
            await service.submit_decision(
                "run_gate", HumanDecision(action="approve", reviewer="analyst")
            )

        message = str(caught.value)
        assert "restarted" in message.lower()
        assert "archive" in message.lower()

    async def test_the_refusal_releases_the_watchlist(
        self, service: RunService, repository: Repository
    ) -> None:
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))
        with pytest.raises(RunNotResumableError):
            await service.submit_decision(
                "run_gate", HumanDecision(action="approve", reviewer="analyst")
            )

        record = await repository.get_run("run_gate")
        assert record is not None
        assert record["status"] == RunStatus.FAILED
        assert await repository.active_run_for(WATCHLIST) is None

    async def test_no_approval_is_recorded_for_a_run_that_cannot_resume(
        self, service: RunService, repository: Repository
    ) -> None:
        """The governance trail must not claim a decision was applied."""
        await _seed(repository, "run_gate", str(RunStatus.AWAITING_APPROVAL))
        with pytest.raises(RunNotResumableError):
            await service.submit_decision(
                "run_gate", HumanDecision(action="approve", reviewer="analyst")
            )

        events = await repository.list_events("run_gate")
        assert all(event.get("kind") != "DECISION_RECORDED" for event in events)
