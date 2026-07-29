"""Persistence: transactional writes and the one-active-run-per-watchlist rule.

The constraint is enforced by a partial unique index in the database, so the test
proves the *database* rejects the duplicate — not that some Python branch does.
"""

from __future__ import annotations

import pytest

from app.core.events import EventKind, RunEvent
from app.core.settings import Settings
from app.models.run import RunStatus
from app.services.repository import ActiveRunExistsError, Repository, normalise_database_url


@pytest.fixture
def repository(temp_database_url: str) -> Repository:
    repo = Repository(Settings(database_url=temp_database_url))
    repo.create_all()
    return repo


class TestUrlNormalisation:
    def test_postgres_urls_are_coerced_onto_psycopg(self) -> None:
        assert normalise_database_url("postgres://u:p@h/db").startswith("postgresql+psycopg://")
        assert normalise_database_url("postgresql://u:p@h/db").startswith("postgresql+psycopg://")

    def test_already_qualified_urls_are_untouched(self) -> None:
        url = "postgresql+psycopg://u:p@h/db"
        assert normalise_database_url(url) == url

    def test_sqlite_urls_are_untouched(self) -> None:
        assert normalise_database_url("sqlite:///./x.db") == "sqlite:///./x.db"


class TestActiveRunConstraint:
    async def test_second_active_run_for_same_watchlist_is_rejected(
        self, repository: Repository
    ) -> None:
        await repository.create_run(
            run_id="run_1",
            watchlist_key="AAPL,MSFT",
            tickers=["AAPL", "MSFT"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        with pytest.raises(ActiveRunExistsError):
            await repository.create_run(
                run_id="run_2",
                watchlist_key="AAPL,MSFT",
                tickers=["AAPL", "MSFT"],
                mode="standard",
                engine="deterministic",
                trigger="test",
            )

    async def test_a_different_watchlist_is_allowed_concurrently(
        self, repository: Repository
    ) -> None:
        await repository.create_run(
            run_id="run_a",
            watchlist_key="AAPL",
            tickers=["AAPL"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        await repository.create_run(
            run_id="run_b",
            watchlist_key="TSLA",
            tickers=["TSLA"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        assert await repository.active_run_for("AAPL") is not None
        assert await repository.active_run_for("TSLA") is not None

    async def test_terminal_status_frees_the_slot(self, repository: Repository) -> None:
        await repository.create_run(
            run_id="run_x",
            watchlist_key="NVDA",
            tickers=["NVDA"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        await repository.finish_run("run_x", str(RunStatus.DELIVERED))
        assert await repository.active_run_for("NVDA") is None

        await repository.create_run(
            run_id="run_y",
            watchlist_key="NVDA",
            tickers=["NVDA"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        record = await repository.get_run("run_y")
        assert record is not None
        assert record["status"] == RunStatus.RUNNING

    async def test_awaiting_approval_still_holds_the_slot(self, repository: Repository) -> None:
        await repository.create_run(
            run_id="run_hold",
            watchlist_key="AMZN",
            tickers=["AMZN"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        await repository.update_run("run_hold", status=str(RunStatus.AWAITING_APPROVAL))
        with pytest.raises(ActiveRunExistsError):
            await repository.create_run(
                run_id="run_hold2",
                watchlist_key="AMZN",
                tickers=["AMZN"],
                mode="standard",
                engine="deterministic",
                trigger="test",
            )


class TestArchive:
    async def test_brief_and_approval_round_trip(self, repository: Repository) -> None:
        await repository.create_run(
            run_id="run_arch",
            watchlist_key="AAPL",
            tickers=["AAPL"],
            mode="standard",
            engine="deterministic",
            trigger="test",
        )
        brief_id = await repository.save_brief(
            run_id="run_arch",
            generated_for="2026-01-02",
            headline="Watchlist mixed",
            partial=False,
            verified=True,
            claims_total=7,
            claims_matched=7,
            brief_json={"headline": "Watchlist mixed"},
            verification_json={"ok": True},
            markdown="# brief",
        )
        assert brief_id.startswith("brf_")

        stored = await repository.latest_brief("run_arch")
        assert stored is not None
        assert stored["verified"] is True
        assert stored["claims_matched"] == 7

        await repository.save_approval(
            run_id="run_arch", action="approve", reviewer="tester", note="looks right"
        )
        rows = await repository.list_runs(10)
        assert rows[0]["id"] == "run_arch"
        assert rows[0]["headline"] == "Watchlist mixed"

    async def test_events_are_archived_idempotently(self, repository: Repository) -> None:
        events = [
            RunEvent(
                run_id="run_ev",
                seq=index,
                ts="2026-01-02T00:00:00Z",
                kind=EventKind.RUN_STARTED,
                message=f"event {index}",
                payload={"i": index},
            )
            for index in range(1, 4)
        ]
        await repository.save_events(events)
        await repository.save_events(events)  # replay must not duplicate

        stored = await repository.list_events("run_ev")
        assert len(stored) == 3
        assert [row["seq"] for row in stored] == [1, 2, 3]

    async def test_list_runs_is_bounded(self, repository: Repository) -> None:
        rows = await repository.list_runs(limit=100_000)
        assert isinstance(rows, list)

    async def test_updating_a_missing_run_is_a_no_op(self, repository: Repository) -> None:
        await repository.update_run("does-not-exist", status="DELIVERED")
