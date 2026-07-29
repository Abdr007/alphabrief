"""Per-run dependencies handed to graph nodes through LangGraph's runtime context.

Nodes stay pure functions of ``(state, config)``; everything with a lifecycle —
the MCP session, the model engine, the event bus, the budget guard — travels in
:class:`RunContext` so a node never reaches for a global.

Delivery uses LangGraph's first-class ``context`` channel
(``StateGraph(..., context_schema=RunContext)`` plus ``ainvoke(..., context=ctx)``).
This matters: values placed in ``config["configurable"]`` are checkpoint state and
are filtered when a checkpointer is attached, whereas the runtime context is
explicitly *not* persisted — which is exactly right for live handles like an open
MCP session.

A ``config``-based fallback is retained so a node can be unit-tested in isolation
by invoking it directly with ``ctx.to_config()``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import get_runtime

from app.core.budget import BudgetGuard
from app.core.claude import LLMEngine
from app.core.events import EventBus, EventKind
from app.core.settings import Settings
from app.core.tracing import Tracer
from app.mcp_server.client import McpToolClient

CONTEXT_KEY = "alphabrief_context"


@dataclass(slots=True)
class RunContext:
    """Everything a node needs that is not state."""

    run_id: str
    settings: Settings
    engine: LLMEngine
    mcp: McpToolClient
    bus: EventBus
    tracer: Tracer
    budget: BudgetGuard
    extras: dict[str, Any] = field(default_factory=dict)

    async def emit(
        self,
        kind: EventKind,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish one telemetry row for this run."""
        await self.bus.publish(self.run_id, kind, message, payload)

    def to_config(self, **extra: Any) -> RunnableConfig:
        """Thread config for the graph. Identity only — deps travel in `context`."""
        configurable: dict[str, Any] = {"thread_id": self.run_id, CONTEXT_KEY: self}
        configurable.update(extra)
        return RunnableConfig(configurable=configurable)


#: Test-only override, so a node can be invoked directly without a live graph.
_override: ContextVar[RunContext | None] = ContextVar("alphabrief_run_context", default=None)


@contextmanager
def use_context(ctx: RunContext) -> Iterator[RunContext]:
    """Bind a :class:`RunContext` for direct node invocation (tests, eval)."""
    token = _override.set(ctx)
    try:
        yield ctx
    finally:
        _override.reset(token)


def current_context() -> RunContext:
    """The :class:`RunContext` the current node is executing under.

    Nodes take no ``config`` parameter: LangGraph's runtime context is the
    supported channel for per-run dependencies, and a ``config`` parameter
    annotated under ``from __future__ import annotations`` is a string at
    inspection time, which LangGraph cannot match — it would be silently ignored.
    """
    override = _override.get()
    if override is not None:
        return override

    try:
        runtime = get_runtime(RunContext)
    except Exception:  # noqa: BLE001 - not running inside a graph
        runtime = None
    if runtime is not None and isinstance(runtime.context, RunContext):
        return runtime.context

    raise RuntimeError(
        "RunContext unavailable — invoke the graph with `context=ctx`, or wrap a "
        "direct node call in `with use_context(ctx): ...`."
    )
