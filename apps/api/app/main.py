"""FastAPI application factory.

One container runs the API, the LangGraph orchestrator and the MCP tool server —
which is what makes the free Hugging Face Spaces / Cloud Run deployment a single
image on port 7860.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.core.middleware import (
    BodyLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.settings import get_settings
from app.core.tracing import get_tracer
from app.services.repository import get_repository
from app.services.runner import get_run_service

logger = logging.getLogger(__name__)

DESCRIPTION = """
Supervisor-pattern multi-agent research orchestration with MCP tooling and
human-in-the-loop governance.

The LLM never does arithmetic. Tools compute over MCP, a deterministic node
recomputes every figure in the final brief, and a human gate signs off —
hallucinated numbers are impossible by construction, not by prompt-begging.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    # These libraries are chatty at INFO and drown the run telemetry.
    for noisy in ("httpx", "httpcore", "urllib3", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create tables on boot; flush traces and cancel live runs on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    repository = get_repository()
    await repository.create_all_async()
    service = get_run_service()
    # Must precede reconciliation: whether a paused run is resumable depends on
    # the durability of the checkpointer, which this call establishes.
    await service.ensure_ready()
    # Runs inherited from a previous process hold their watchlist's single active
    # slot forever unless they are closed here.
    await service.reconcile_orphans()
    # Mint the approval token now rather than on the first authenticated request.
    # Locally the console reads it from disk to authenticate, so leaving it lazy
    # deadlocks: the file only appears after an approval that cannot succeed
    # without it.
    settings.require_approval_token()
    logger.info(
        "AlphaBrief %s ready — engine=%s mcp=%s langfuse=%s smtp=%s checkpoints=%s",
        __version__,
        settings.resolved_engine,
        settings.mcp_transport,
        settings.langfuse_enabled,
        settings.smtp_enabled,
        "durable" if service.durable_checkpoints else "in-memory",
    )
    app.state.settings = settings
    try:
        yield
    finally:
        await get_run_service().shutdown()
        get_tracer().shutdown()
        repository.dispose()


def create_app() -> FastAPI:
    """Build the ASGI application."""
    settings = get_settings()
    app = FastAPI(
        title=settings.api_title,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # Order matters: body limit runs outermost so oversized payloads are rejected
    # before anything else touches them.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=30, window_seconds=60.0)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return field-level detail without echoing the raw submitted body."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": "Request validation failed.",
                "errors": [
                    {
                        "field": ".".join(str(part) for part in err.get("loc", ())),
                        "message": err.get("msg", "invalid value"),
                    }
                    for err in exc.errors()[:20]
                ],
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, _exc: Exception) -> JSONResponse:
        """Never leak internals: log the detail, return an opaque error."""
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )

    return app


app = create_app()
