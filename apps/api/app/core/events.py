"""In-process event bus that powers the live SSE agent-activity feed.

Every graph step, MCP tool call and verification claim is published here. The UI
subscribes per run; late subscribers replay the backlog first so a browser that
connects mid-run still sees the whole telemetry trace.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from app.core.settings import get_settings

_SENTINEL: Final = object()


class EventKind(StrEnum):
    """Telemetry event taxonomy rendered by the RUN console."""

    RUN_STARTED = "run.started"
    SUPERVISOR_PLAN = "supervisor.plan"
    SUPERVISOR_ROUTE = "supervisor.route"
    AGENT_DISPATCH = "agent.dispatch"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    MODEL_CALL = "model.call"
    MCP_TOOL_CALL = "mcp.tool_call"
    STATE_PROGRESS = "state.progress"
    WRITER_STARTED = "writer.started"
    WRITER_COMPLETED = "writer.completed"
    VERIFY_STARTED = "verify.started"
    VERIFY_CLAIM = "verify.claim"
    VERIFY_COMPLETED = "verify.completed"
    GATE_AWAITING = "gate.awaiting"
    GATE_DECISION = "gate.decision"
    DELIVERY_SENT = "delivery.sent"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class RunEvent:
    """A single telemetry row."""

    run_id: str
    seq: int
    ts: str
    kind: EventKind
    message: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": self.ts,
            "kind": str(self.kind),
            "message": self.message,
            "payload": self.payload,
        }


TERMINAL_KINDS: Final[frozenset[EventKind]] = frozenset(
    {EventKind.RUN_COMPLETED, EventKind.RUN_FAILED}
)


@dataclass(slots=True)
class _RunChannel:
    backlog: deque[RunEvent]
    subscribers: set[asyncio.Queue[RunEvent | object]] = field(default_factory=set)
    seq: int = 0
    closed: bool = False


class EventBus:
    """Fan-out bus with bounded per-run replay backlog."""

    def __init__(self, max_events_per_run: int | None = None) -> None:
        limit = max_events_per_run or get_settings().max_events_per_run
        self._max_events = limit
        self._channels: dict[str, _RunChannel] = {}
        self._lock = asyncio.Lock()

    def _channel(self, run_id: str) -> _RunChannel:
        channel = self._channels.get(run_id)
        if channel is None:
            channel = _RunChannel(backlog=deque(maxlen=self._max_events))
            self._channels[run_id] = channel
        return channel

    async def publish(
        self,
        run_id: str,
        kind: EventKind,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        """Append an event and fan it out to every live subscriber."""
        async with self._lock:
            channel = self._channel(run_id)
            channel.seq += 1
            event = RunEvent(
                run_id=run_id,
                seq=channel.seq,
                ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                kind=kind,
                message=message,
                payload=payload or {},
            )
            channel.backlog.append(event)
            subscribers = list(channel.subscribers)
            if kind in TERMINAL_KINDS:
                channel.closed = True

        for queue in subscribers:
            # Unbounded queues: a slow SSE client must never stall the graph.
            queue.put_nowait(event)
        if kind in TERMINAL_KINDS:
            for queue in subscribers:
                queue.put_nowait(_SENTINEL)
        return event

    async def history(self, run_id: str) -> list[RunEvent]:
        async with self._lock:
            channel = self._channels.get(run_id)
            return list(channel.backlog) if channel else []

    async def is_closed(self, run_id: str) -> bool:
        async with self._lock:
            channel = self._channels.get(run_id)
            return bool(channel and channel.closed)

    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Yield backlog then live events until the run reaches a terminal state."""
        queue: asyncio.Queue[RunEvent | object] = asyncio.Queue()
        async with self._lock:
            channel = self._channel(run_id)
            backlog = list(channel.backlog)
            already_closed = channel.closed
            if not already_closed:
                channel.subscribers.add(queue)

        try:
            for event in backlog:
                yield event
            if already_closed:
                return
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    return
                if isinstance(item, RunEvent):
                    yield item
        finally:
            async with self._lock:
                existing = self._channels.get(run_id)
                if existing is not None:
                    existing.subscribers.discard(queue)

    async def forget(self, run_id: str) -> None:
        """Drop a run's backlog (called after archival)."""
        async with self._lock:
            self._channels.pop(run_id, None)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide event bus singleton."""
    global _bus  # noqa: PLW0603 - deliberate process-wide singleton
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """Test hook: drop all buffered runs."""
    global _bus  # noqa: PLW0603 - deliberate process-wide singleton
    _bus = None
