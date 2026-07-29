"""Human-in-the-loop gate — a real LangGraph interrupt over a checkpointer.

The graph *pauses here*. Execution stops mid-run, the state is checkpointed, and
the process can go away entirely. When a human approves, rejects or edits in the
UI, the graph is resumed from the checkpoint with their decision.

Financial output never auto-sends: there is no path from the verifier to the
delivery node that does not pass through this node's `interrupt`.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import ValidationError

from app.core.events import EventKind
from app.graph.context import current_context
from app.graph.state import RunState
from app.models.brief import validate_narrative
from app.models.run import HumanDecision, RunError, RunStatus

logger = logging.getLogger(__name__)


def build_gate_payload(state: RunState) -> dict[str, Any]:
    """What the reviewer sees: the brief plus the full verification report."""
    brief = state.get("brief")
    report = state.get("verification") or {}
    return {
        "run_id": state.get("run_id"),
        "session_date": state.get("session_date"),
        "verified": bool(report.get("ok")),
        "requires_review": not bool(report.get("ok")),
        "brief": brief.model_dump() if brief is not None else None,
        "verification": report,
        "partial": bool(brief.partial) if brief is not None else True,
        "errors": [error.model_dump() for error in state.get("errors", [])],
    }


def _apply_edits(state: RunState, decision: HumanDecision) -> tuple[Any, list[RunError]]:
    """Apply reviewer edits to narrative fields only. Numbers are untouchable."""
    brief = state.get("brief")
    if brief is None or decision.action != "edit":
        return brief, []

    errors: list[RunError] = []
    updates: dict[str, str] = {}
    for field_name, value in (
        ("headline", decision.edited_headline),
        ("executive_summary", decision.edited_summary),
    ):
        if value is None:
            continue
        try:
            updates[field_name] = validate_narrative(value, f"edited {field_name}")
        except ValueError as exc:
            errors.append(
                RunError(
                    stage="gate",
                    message=f"reviewer edit to {field_name} rejected: {exc}",
                    severity="warning",
                )
            )
    if not updates:
        return brief, errors
    return brief.model_copy(update=updates), errors


async def gate_node(state: RunState) -> dict[str, Any]:
    """Graph node: pause for human approval, then record the decision.

    Note: LangGraph re-executes a node from the top when its interrupt resumes,
    so this function deliberately performs no side effects before `interrupt()`.
    The "awaiting approval" telemetry is emitted by the runner when it observes
    the interrupt, which keeps the feed free of duplicates.
    """
    ctx = current_context()
    raw_decision = interrupt(build_gate_payload(state))

    try:
        decision = (
            raw_decision
            if isinstance(raw_decision, HumanDecision)
            else HumanDecision.model_validate(raw_decision)
        )
    except ValidationError as exc:
        logger.warning("invalid human decision payload: %s", exc)
        decision = HumanDecision(action="reject", note=f"invalid decision payload: {exc}")

    brief, edit_errors = _apply_edits(state, decision)

    approved = decision.action in ("approve", "edit")
    status = RunStatus.APPROVED if approved else RunStatus.REJECTED

    await ctx.emit(
        EventKind.GATE_DECISION,
        f"Human {decision.action}d the brief",
        {
            "action": decision.action,
            "reviewer": decision.reviewer,
            "note": decision.note,
            "approved": approved,
        },
    )

    update: dict[str, Any] = {"decision": decision, "status": status, "errors": edit_errors}
    if brief is not None:
        update["brief"] = brief
    return update


def route_after_gate(state: RunState) -> Literal["deliver", "__end__"]:
    """Approved briefs proceed to delivery; rejected ones stop here."""
    decision = state.get("decision")
    if decision is not None and decision.action in ("approve", "edit"):
        return "deliver"
    return "__end__"
