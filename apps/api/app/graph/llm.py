"""One place where a model call happens — traced, budgeted and telemetered.

Every node goes through :func:`call_model`, so there is exactly one code path
that charges the budget guard, records the Langfuse generation, and publishes the
`model.call` telemetry row. Nothing can call Claude and skip the guard.
"""

from __future__ import annotations

import logging

from app.core.budget import BudgetExceededError
from app.core.claude import LLMRequest, LLMResult
from app.core.events import EventKind
from app.graph.context import RunContext
from app.models.run import TokenSpend

logger = logging.getLogger(__name__)


class ModelCallOutcome:
    """Result of a guarded model call, plus the spend delta for state."""

    __slots__ = ("result", "spend")

    def __init__(self, result: LLMResult, spend: TokenSpend) -> None:
        self.result = result
        self.spend = spend


async def call_model(ctx: RunContext, request: LLMRequest) -> ModelCallOutcome:
    """Execute one model call under the budget guard, tracing it end to end.

    Raises:
        BudgetExceededError: when the run has exhausted its token or USD budget.
            Nodes convert this into a ``BUDGET_ABORT`` status rather than crashing.
    """
    model = ctx.engine.model_for(request.role)
    max_tokens = request.max_tokens or ctx.settings.llm_max_tokens

    # Pre-flight: refuse a call whose worst case cannot fit in the remaining budget.
    ctx.budget.ensure_headroom(model, max_tokens)

    await ctx.emit(
        EventKind.MODEL_CALL,
        f"{request.role} → {model}",
        {"role": str(request.role), "model": model, "hint": str(request.hint)},
    )

    with ctx.tracer.step(
        f"model:{request.role}",
        run_id=ctx.run_id,
        as_type="generation",
        input_data={"hint": str(request.hint), "system": request.system[:400]},
        metadata={"model": model, "engine": ctx.engine.name},
    ) as span:
        result = await ctx.engine.complete(request)
        span.update(output={"text": result.text[:800], "tool_calls": len(result.tool_calls)})

    cost = result.usage.cost_usd(result.model)
    try:
        snapshot = ctx.budget.charge(result.model, result.usage)
    except BudgetExceededError:
        await ctx.emit(
            EventKind.WARNING,
            "Per-run token budget exhausted — aborting",
            {"model": result.model},
        )
        raise

    ctx.tracer.record_generation(
        f"model:{request.role}",
        run_id=ctx.run_id,
        model=result.model,
        input_data={"hint": str(request.hint)},
        output_data=result.text[:2000],
        usage=result.usage,
        cost_usd=cost,
        metadata={"engine": result.engine, "stop_reason": result.stop_reason},
    )

    spend = TokenSpend(
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cache_read_tokens=result.usage.cache_read_tokens,
        cache_write_tokens=result.usage.cache_write_tokens,
        cost_usd=cost,
        calls=1,
    )
    logger.debug(
        "model call %s/%s tokens=%s cost=%.6f budget_remaining=%.4f",
        request.role,
        request.hint,
        result.usage.total_tokens,
        cost,
        snapshot.remaining_usd,
    )
    return ModelCallOutcome(result=result, spend=spend)
