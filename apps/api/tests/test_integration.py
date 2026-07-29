"""End-to-end: the whole governance chain, against live market data.

trigger → supervisor → parallel workers → writer → verify → human gate → deliver

Every assertion here is about the *chain*, not about a particular market value:
that a figure reconciles, that a fault is caught, that nothing ships without a
human. Those hold whatever the market did today.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.settings import Settings
from app.models.run import HumanDecision, RunMode, RunStatus
from app.services.repository import ActiveRunExistsError
from app.services.runner import RunService

SETTLE_TIMEOUT_S = 180.0


async def _settle(service: RunService, run_id: str) -> dict[str, Any]:
    """Wait until the run leaves RUNNING."""
    deadline = asyncio.get_running_loop().time() + SETTLE_TIMEOUT_S
    while asyncio.get_running_loop().time() < deadline:
        record = await service.get_run(run_id)
        if record and record["status"] not in ("RUNNING", "QUEUED"):
            return record
        await asyncio.sleep(0.25)
    pytest.fail(f"run {run_id} did not settle within {SETTLE_TIMEOUT_S}s")


@pytest.fixture
def service(settings: Settings) -> RunService:
    return RunService(settings=settings)


class TestHappyPath:
    async def test_full_chain_from_trigger_to_delivery(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["AAPL"], trigger="test")
        record = await _settle(service, run_id)

        # 1. It paused for a human rather than delivering itself.
        assert record["status"] == RunStatus.AWAITING_APPROVAL

        gate = await service.gate_payload(run_id)
        assert gate is not None
        report = gate["verification"]

        # 2. Every numeric claim was recomputed and matched.
        assert gate["verified"] is True
        assert report["checked_claims"] > 0
        assert report["matched"] == report["checked_claims"]
        assert report["mismatched"] == 0
        assert report["coverage"] == 1.0

        # 3. The brief is well-formed and renders.
        assert gate["brief"]["snapshot"]
        assert gate["markdown"].startswith("# AlphaBrief")

        # 4. Approval releases it.
        result = await service.submit_decision(
            run_id, HumanDecision(action="approve", reviewer="tester")
        )
        assert result["status"] == RunStatus.DELIVERED
        final = await service.get_run(run_id)
        assert final is not None
        assert final["status"] == RunStatus.DELIVERED
        assert final["verified"] is True
        assert final["iterations"] >= 1

    async def test_telemetry_covers_the_whole_chain(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["MSFT"], trigger="test")
        await _settle(service, run_id)
        kinds = {str(event.kind) for event in await service.bus.history(run_id)}

        for expected in (
            "run.started",
            "supervisor.plan",
            "agent.started",
            "agent.completed",
            "mcp.tool_call",
            "writer.started",
            "verify.started",
            "verify.claim",
            "verify.completed",
            "gate.awaiting",
        ):
            assert expected in kinds, f"missing telemetry: {expected}"

        await service.submit_decision(run_id, HumanDecision(action="approve"))

    async def test_tool_calls_carry_arguments_and_timing(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["NVDA"], trigger="test")
        await _settle(service, run_id)
        events = [e for e in await service.bus.history(run_id) if str(e.kind) == "mcp.tool_call"]

        assert events
        for event in events:
            assert "tool" in event.payload
            assert "arguments" in event.payload
            assert isinstance(event.payload["duration_ms"], float)

        await service.submit_decision(run_id, HumanDecision(action="approve"))


class TestHumanGate:
    async def test_rejection_blocks_delivery(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["TSLA"], trigger="test")
        await _settle(service, run_id)

        result = await service.submit_decision(
            run_id, HumanDecision(action="reject", note="not today")
        )
        assert result["status"] == RunStatus.REJECTED

        record = await service.get_run(run_id)
        assert record is not None
        assert record["status"] == RunStatus.REJECTED

    async def test_edit_applies_narrative_only(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["AMZN"], trigger="test")
        await _settle(service, run_id)

        await service.submit_decision(
            run_id,
            HumanDecision(
                action="edit",
                edited_headline="Reviewer rewrote this headline",
                reviewer="tester",
            ),
        )
        stored = await service.repository.latest_brief(run_id)
        assert stored is not None
        assert stored["brief"]["headline"] == "Reviewer rewrote this headline"

    async def test_an_edit_containing_a_numeral_is_refused(self, service: RunService) -> None:
        """Reviewers may reword; they may not introduce an unverified figure."""
        run_id = await service.start_run(tickers=["META"], trigger="test")
        await _settle(service, run_id)

        gate_before = await service.gate_payload(run_id)
        assert gate_before is not None
        original = gate_before["brief"]["headline"]

        await service.submit_decision(
            run_id,
            HumanDecision(action="edit", edited_headline="Stock jumped 42 percent today"),
        )
        stored = await service.repository.latest_brief(run_id)
        assert stored is not None
        assert stored["brief"]["headline"] == original

    async def test_decision_on_an_unknown_run_is_refused(self, service: RunService) -> None:
        from app.services.runner import RunNotFoundError

        with pytest.raises(RunNotFoundError):
            await service.submit_decision("run_does_not_exist", HumanDecision(action="approve"))

    async def test_a_second_decision_is_refused(self, service: RunService) -> None:
        from app.services.runner import RunNotFoundError

        run_id = await service.start_run(tickers=["GOOGL"], trigger="test")
        await _settle(service, run_id)
        await service.submit_decision(run_id, HumanDecision(action="approve"))

        with pytest.raises(RunNotFoundError):
            await service.submit_decision(run_id, HumanDecision(action="approve"))


class TestFaultInjectionModes:
    async def test_demo_fault_degrades_without_crashing(self, service: RunService) -> None:
        """The interview kill shot: a fake ticker must not break the run."""
        run_id = await service.start_run(tickers=["AAPL"], mode=RunMode.DEMO_FAULT)
        record = await _settle(service, run_id)

        assert record["status"] == RunStatus.AWAITING_APPROVAL
        assert record["status"] != RunStatus.FAILED
        assert record["partial"] is True
        assert record["error_count"] > 0

        gate = await service.gate_payload(run_id)
        assert gate is not None
        brief = gate["brief"]
        assert brief["partial"] is True
        assert brief["data_gaps"]
        # The dead ticker is in the watchlist but not in the snapshot.
        assert "ZZZZQQQQ" in brief["watchlist"]
        assert "ZZZZQQQQ" not in [row["ticker"] for row in brief["snapshot"]]
        # Everything that *did* resolve still verified cleanly.
        assert gate["verified"] is True

        await service.submit_decision(run_id, HumanDecision(action="approve"))

    async def test_demo_mismatch_is_caught_and_escalated(self, service: RunService) -> None:
        """Injected bad figure: one regeneration, then HUMAN_REVIEW. Never delivered."""
        run_id = await service.start_run(tickers=["MSFT"], mode=RunMode.DEMO_MISMATCH)
        record = await _settle(service, run_id)

        assert record["status"] == RunStatus.HUMAN_REVIEW

        gate = await service.gate_payload(run_id)
        assert gate is not None
        assert gate["verified"] is False
        assert gate["requires_review"] is True
        assert gate["verification"]["mismatched"] >= 1

        events = await service.bus.history(run_id)
        # Exactly one regeneration was attempted before escalating.
        assert sum(1 for e in events if str(e.kind) == "writer.started") == 2
        assert sum(1 for e in events if str(e.kind) == "verify.completed") == 2

        await service.submit_decision(run_id, HumanDecision(action="reject", note="bad numbers"))
        final = await service.get_run(run_id)
        assert final is not None
        assert final["status"] == RunStatus.REJECTED


class TestConcurrencyGuard:
    async def test_one_active_run_per_watchlist(self, service: RunService) -> None:
        run_id = await service.start_run(tickers=["INTC"], trigger="test")
        with pytest.raises(ActiveRunExistsError):
            await service.start_run(tickers=["intc"], trigger="test")

        await _settle(service, run_id)
        await service.submit_decision(run_id, HumanDecision(action="approve"))

        # Slot released.
        second = await service.start_run(tickers=["INTC"], trigger="test")
        await _settle(service, second)
        await service.submit_decision(second, HumanDecision(action="approve"))
