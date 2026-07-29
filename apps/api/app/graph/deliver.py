"""Delivery node — email the approved brief.

Reached only from an approved human gate. Archival to Neon Postgres is performed
by the run service around the graph, so a delivery failure can never lose the
audit record.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.events import EventKind
from app.graph.context import current_context
from app.graph.state import RunState
from app.models.run import RunError, RunStatus
from app.services.email import send_brief

logger = logging.getLogger(__name__)


async def deliver_node(state: RunState) -> dict[str, Any]:
    """Graph node: send the approved brief and record the delivery outcome."""
    ctx = current_context()
    brief = state.get("brief")
    if brief is None:
        return {
            "status": RunStatus.FAILED,
            "errors": [RunError(stage="deliver", message="no brief to deliver")],
        }

    with ctx.tracer.step("deliver", run_id=ctx.run_id) as span:
        result = await send_brief(brief, ctx.settings)
        span.update(output=result.to_dict())

    errors: list[RunError] = []
    if not result.sent and result.channel == "smtp":
        errors.append(RunError(stage="deliver", message=result.detail, severity="warning"))

    await ctx.emit(
        EventKind.DELIVERY_SENT,
        result.detail,
        result.to_dict(),
    )
    return {
        "status": RunStatus.DELIVERED,
        "delivery": result.to_dict(),
        "errors": errors,
    }
