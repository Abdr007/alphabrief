"""MCP client the agents use. Tools are consumed over MCP, never called directly.

Two transports, same protocol and same tool schemas:

``stdio`` (default)
    Spawns ``python -m app.mcp_server`` as a child process and speaks JSON-RPC
    over its stdin/stdout — exactly how any other MCP client would connect.

``inmemory``
    Runs the same :class:`MCPServer` object over in-memory streams. Still the
    real MCP protocol; used by tests and the eval harness to avoid paying process
    spawn cost 20 times over.

The client re-enforces the tool whitelist. Even a compromised agent cannot ask
for a tool the server does not expose, and cannot reach anything but these four.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from mcp import Client, StdioServerParameters, stdio_client

from app.core.settings import Settings, get_settings
from app.mcp_server.providers import ProviderContext
from app.mcp_server.registry import TOOL_NAMES, build_server
from app.models.market import Fundamentals, Metrics, NewsFeed, PriceBar, PriceHistory

logger = logging.getLogger(__name__)

#: apps/api — the directory that must be importable for `-m app.mcp_server`.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class McpToolError(RuntimeError):
    """A tool call could not be completed at the protocol level."""


_shared_server: Any | None = None


def shared_inmemory_server(settings: Settings) -> Any:
    """One process-wide in-memory MCP server, so its provider cache is shared.

    Opt-in via ``MCP_SHARED_SERVER``. The eval harness uses it so twenty runs
    make a handful of provider calls rather than a hundred — the graph, the MCP
    protocol and the verifier are all still exercised in full.
    """
    global _shared_server  # noqa: PLW0603 - deliberate process-wide singleton
    if _shared_server is None:
        _shared_server = build_server(
            ProviderContext(min_interval_seconds=settings.provider_min_interval_seconds)
        )
    return _shared_server


def reset_shared_server() -> None:
    """Test hook: drop the shared server and its cache."""
    global _shared_server  # noqa: PLW0603 - deliberate process-wide singleton
    _shared_server = None


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """Telemetry row for one MCP tool call — args and ms timing, as the UI shows."""

    tool: str
    arguments: dict[str, Any]
    duration_ms: float
    ok: bool
    summary: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "duration_ms": round(self.duration_ms, 1),
            "ok": self.ok,
            "summary": self.summary,
            "error": self.error,
        }


@dataclass(slots=True)
class _Args:
    """Compact, log-safe rendering of tool arguments for telemetry."""

    raw: dict[str, Any]

    def compact(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.raw.items():
            if isinstance(value, list):
                out[key] = f"[{len(value)} items]"
            elif isinstance(value, str) and len(value) > 80:
                out[key] = value[:77] + "…"
            else:
                out[key] = value
        return out


@dataclass(slots=True)
class McpToolClient:
    """Async context manager wrapping one MCP session for one run.

    `emitter` lets the run publish each tool call to the live telemetry feed the
    instant it completes, so the UI shows calls scrolling rather than arriving in
    batches. `collect()` gives a node its own record slice without racing the
    other agent running in the same superstep.
    """

    settings: Settings = field(default_factory=get_settings)
    records: list[ToolCallRecord] = field(default_factory=list)
    emitter: Callable[[ToolCallRecord], Awaitable[None]] | None = None
    _sinks: list[list[ToolCallRecord]] = field(default_factory=list)
    _client: Client | None = None
    _stack: Any = None

    @contextmanager
    def collect(self) -> Iterator[list[ToolCallRecord]]:
        """Capture only the tool calls made inside this block."""
        sink: list[ToolCallRecord] = []
        self._sinks.append(sink)
        try:
            yield sink
        finally:
            if sink in self._sinks:
                self._sinks.remove(sink)

    async def _record(self, record: ToolCallRecord) -> None:
        self.records.append(record)
        for sink in self._sinks:
            sink.append(record)
        if self.emitter is not None:
            await self.emitter(record)

    async def __aenter__(self) -> Self:
        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        self._stack = stack
        transport = self.settings.mcp_transport
        try:
            if transport == "inmemory":
                server = (
                    shared_inmemory_server(self.settings)
                    if self.settings.mcp_shared_server
                    else build_server(
                        ProviderContext(
                            min_interval_seconds=self.settings.provider_min_interval_seconds
                        )
                    )
                )
                client = Client(server, read_timeout_seconds=self.settings.mcp_call_timeout_seconds)
            else:
                env = dict(os.environ)
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{PACKAGE_ROOT}{os.pathsep}{existing}" if existing else str(PACKAGE_ROOT)
                )
                env["PROVIDER_MIN_INTERVAL_SECONDS"] = str(
                    self.settings.provider_min_interval_seconds
                )
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "app.mcp_server"],
                    env=env,
                    cwd=str(PACKAGE_ROOT),
                )
                client = Client(
                    stdio_client(params),
                    read_timeout_seconds=self.settings.mcp_call_timeout_seconds,
                )
            self._client = await stack.enter_async_context(client)
        except Exception:
            await stack.aclose()
            self._stack = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        self._client = None
        if stack is not None:
            await stack.aclose()

    # ------------------------------------------------------------------ raw ---
    async def list_tool_names(self) -> list[str]:
        """Names advertised by the connected server (discoverability check)."""
        client = self._require_client()
        listing = await client.list_tools()
        return sorted(tool.name for tool in listing.tools)

    async def list_tool_specs(self) -> list[dict[str, Any]]:
        """Full advertised tool documentation, exactly as any MCP client sees it."""
        client = self._require_client()
        listing = await client.list_tools()
        specs: list[dict[str, Any]] = []
        for tool in sorted(listing.tools, key=lambda t: t.name):
            schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
            specs.append(
                {
                    "name": tool.name,
                    "description": (tool.description or "").strip(),
                    "input_schema": dict(schema),
                }
            )
        return specs

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one whitelisted MCP tool and return its structured payload."""
        if name not in TOOL_NAMES:
            raise McpToolError(f"tool '{name}' is not on the AlphaBrief MCP whitelist")
        client = self._require_client()
        started = time.perf_counter()
        try:
            result = await client.call_tool(name, arguments)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            await self._record(
                ToolCallRecord(
                    tool=name,
                    arguments=_Args(arguments).compact(),
                    duration_ms=elapsed,
                    ok=False,
                    summary="transport error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise McpToolError(f"MCP call to '{name}' failed: {exc}") from exc

        elapsed = (time.perf_counter() - started) * 1000
        try:
            payload = _extract_payload(result)
        except McpToolError as exc:
            await self._record(
                ToolCallRecord(
                    tool=name,
                    arguments=_Args(arguments).compact(),
                    duration_ms=elapsed,
                    ok=False,
                    summary="tool error",
                    error=str(exc),
                )
            )
            raise

        tool_error = payload.get("error") if isinstance(payload, dict) else None
        await self._record(
            ToolCallRecord(
                tool=name,
                arguments=_Args(arguments).compact(),
                duration_ms=elapsed,
                ok=tool_error is None,
                summary=_summarise(name, payload),
                error=str(tool_error) if tool_error else None,
            )
        )
        return payload

    # -------------------------------------------------------------- typed api ---
    async def get_price_history(self, ticker: str, days: int) -> PriceHistory:
        return PriceHistory.model_validate(
            await self.call("get_price_history", {"ticker": ticker, "days": days})
        )

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals.model_validate(await self.call("get_fundamentals", {"ticker": ticker}))

    async def compute_metrics(
        self, ticker: str, bars: list[PriceBar], pe_ratio: float | None
    ) -> Metrics:
        payload = await self.call(
            "compute_metrics",
            {
                "ticker": ticker,
                "bars": [bar.model_dump() for bar in bars],
                "pe_ratio": pe_ratio,
            },
        )
        return Metrics.model_validate(payload)

    async def fetch_rss_news(self, ticker: str, limit: int) -> NewsFeed:
        return NewsFeed.model_validate(
            await self.call("fetch_rss_news", {"ticker": ticker, "limit": limit})
        )

    # -------------------------------------------------------------- internal ---
    def _require_client(self) -> Client:
        if self._client is None:
            raise McpToolError("MCP client is not connected; use `async with McpToolClient()`")
        return self._client


def _extract_payload(result: Any) -> dict[str, Any]:
    """Pull the structured dict out of an MCP CallToolResult.

    A protocol-level tool error (``isError``) is raised rather than silently
    returned, so a server-side failure can never be mistaken for empty data.
    """
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    if is_error:
        detail = " ".join(
            str(getattr(block, "text", "")) for block in (getattr(result, "content", []) or [])
        ).strip()
        raise McpToolError(detail or "MCP tool reported an error with no detail")

    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # MCPServer wraps non-object returns under "result"; models come through flat.
        inner = structured.get("result")
        if isinstance(inner, dict):
            return inner
        return structured

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise McpToolError("MCP tool returned no structured payload")


def _summarise(tool: str, payload: dict[str, Any]) -> str:
    """One-line human summary rendered in the live telemetry feed."""
    if payload.get("error"):
        return str(payload["error"])[:120]
    if tool == "get_price_history":
        return f"{len(payload.get('bars', []))} daily bars"
    if tool == "get_fundamentals":
        return f"{payload.get('name') or payload.get('ticker')} · P/E {payload.get('pe_ratio')}"
    if tool == "compute_metrics":
        return (
            f"close {payload.get('last_close')} · 30d {payload.get('return_30d_pct')}% "
            f"· vol {payload.get('volatility_annualised_pct')}%"
        )
    if tool == "fetch_rss_news":
        return f"{len(payload.get('items', []))} headlines"
    return "ok"
