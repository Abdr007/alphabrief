"""HTTP routes: trigger a run, stream telemetry, approve, browse the archive."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app import __version__
from app.api.schemas import (
    CreateRunRequest,
    CreateRunResponse,
    DecisionRequest,
    DecisionResponse,
    HealthResponse,
    RunSummary,
    ToolDoc,
)
from app.core.events import EventKind, get_event_bus
from app.core.security import require_approval_token
from app.core.settings import get_settings
from app.mcp_server.client import McpToolClient
from app.models.run import HumanDecision
from app.services.repository import ActiveRunExistsError
from app.services.runner import (
    RunNotFoundError,
    RunNotResumableError,
    RunService,
    get_run_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Bound concurrent SSE subscribers so a browser loop cannot exhaust the process.
MAX_STREAM_CLIENTS = 64
_stream_clients = 0
_stream_lock = asyncio.Lock()

#: SSE keep-alive interval, well under typical proxy idle timeouts.
SSE_PING_SECONDS = 15


def service() -> RunService:
    return get_run_service()


# --------------------------------------------------------------------------- #
# Health & discovery                                                           #
# --------------------------------------------------------------------------- #


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness plus a readable summary of how this process is configured."""
    settings = get_settings()
    database = "postgres" if "postgres" in settings.database_url else "sqlite"
    return HealthResponse(
        status="ok",
        version=__version__,
        engine=settings.resolved_engine,
        mcp_transport=settings.mcp_transport,
        langfuse=settings.langfuse_enabled,
        smtp=settings.smtp_enabled,
        database=database,
        # Observed, not inferred: the durable upgrade can fail and fall back.
        durable_checkpoints=get_run_service().durable_checkpoints,
        watchlist=settings.watchlist,
        models={
            "supervisor": settings.model_supervisor,
            "worker": settings.model_worker,
            "writer": settings.model_writer,
        },
    )


@router.get("/v1/mcp/tools", response_model=list[ToolDoc], tags=["mcp"])
async def mcp_tools() -> list[ToolDoc]:
    """List the MCP tools this deployment exposes, with their JSON schemas.

    Tools are *discoverable*: this is the same listing any MCP client would get,
    which is the point of putting the tool layer behind MCP in the first place.
    """
    settings = get_settings()
    async with McpToolClient(settings=settings) as client:
        specs = await client.list_tool_specs()
    return [ToolDoc.model_validate(spec) for spec in specs]


# --------------------------------------------------------------------------- #
# Runs                                                                         #
# --------------------------------------------------------------------------- #


@router.post(
    "/v1/runs",
    response_model=CreateRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def create_run(
    payload: CreateRunRequest,
    svc: Annotated[RunService, Depends(service)],
) -> CreateRunResponse:
    """Trigger a research run. Returns immediately; watch `stream_url` for progress."""
    tickers = payload.resolved_tickers()
    try:
        run_id = await svc.start_run(tickers=tickers, mode=payload.mode, trigger="api")
    except ActiveRunExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    settings = get_settings()
    return CreateRunResponse(
        run_id=run_id,
        status="RUNNING",
        tickers=tickers,
        mode=str(payload.mode),
        engine=settings.resolved_engine,
        stream_url=f"/v1/runs/{run_id}/stream",
    )


@router.get("/v1/runs", response_model=list[RunSummary], tags=["runs"])
async def list_runs(
    svc: Annotated[RunService, Depends(service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunSummary]:
    """Archive: past runs with cost, latency and iteration count."""
    rows = await svc.list_runs(limit)
    return [RunSummary.model_validate(row) for row in rows]


@router.get("/v1/runs/{run_id}", tags=["runs"])
async def get_run(run_id: str, svc: Annotated[RunService, Depends(service)]) -> dict[str, Any]:
    record = await svc.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return record


@router.get("/v1/runs/{run_id}/gate", tags=["runs"])
async def get_gate(run_id: str, svc: Annotated[RunService, Depends(service)]) -> dict[str, Any]:
    """The approval view: the brief plus its full verification report."""
    payload = await svc.gate_payload(run_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No brief is available for this run yet.",
        )
    return payload


@router.get("/v1/runs/{run_id}/events", tags=["runs"])
async def get_events(
    run_id: str, svc: Annotated[RunService, Depends(service)]
) -> list[dict[str, Any]]:
    """Replay archived telemetry for a finished run."""
    live = await get_event_bus().history(run_id)
    if live:
        return [event.to_dict() for event in live]
    return await svc.repository.list_events(run_id)


@router.get("/v1/runs/{run_id}/stream", tags=["runs"])
async def stream_run(run_id: str, request: Request) -> EventSourceResponse:
    """Live agent-activity feed over Server-Sent Events."""
    global _stream_clients  # noqa: PLW0603 - process-wide connection budget
    async with _stream_lock:
        if _stream_clients >= MAX_STREAM_CLIENTS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Too many live viewers; try again shortly.",
            )
        _stream_clients += 1

    bus = get_event_bus()

    async def publisher() -> AsyncIterator[dict[str, str]]:
        global _stream_clients  # noqa: PLW0603 - process-wide connection budget
        try:
            async for event in bus.subscribe(run_id):
                if await request.is_disconnected():
                    break
                yield {
                    "event": str(event.kind),
                    "id": str(event.seq),
                    "data": _dump(event.to_dict()),
                }
            yield {"event": "stream.end", "data": '{"done":true}'}
        finally:
            async with _stream_lock:
                _stream_clients = max(0, _stream_clients - 1)

    return EventSourceResponse(
        publisher(),
        ping=SSE_PING_SECONDS,
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/v1/runs/{run_id}/decision", response_model=DecisionResponse, tags=["approval"])
async def submit_decision(
    run_id: str,
    payload: DecisionRequest,
    svc: Annotated[RunService, Depends(service)],
    _: Annotated[None, Depends(require_approval_token)],
) -> DecisionResponse:
    """Approve, edit or reject a brief. Requires the approval bearer token."""
    decision = HumanDecision(
        action=payload.action,
        reviewer=payload.reviewer,
        note=payload.note,
        edited_headline=payload.edited_headline,
        edited_summary=payload.edited_summary,
    )
    try:
        result = await svc.submit_decision(run_id, decision)
    except RunNotResumableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return DecisionResponse(run_id=run_id, status=str(result.get("status")), action=payload.action)


def _dump(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str, separators=(",", ":"))


@router.get("/v1/events/kinds", tags=["system"])
async def event_kinds() -> JSONResponse:
    """The telemetry taxonomy, so the UI can render unknown kinds gracefully."""
    return JSONResponse([str(kind) for kind in EventKind])
