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
