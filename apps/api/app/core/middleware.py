"""Defence-in-depth middleware.

Three independent layers, each cheap and each covering a different failure mode:

``SecurityHeadersMiddleware``
    Standard hardening headers on every response, including a CSP that forbids
    script execution in API responses outright.

``BodyLimitMiddleware``
    Rejects oversized request bodies before FastAPI attempts to parse them, using
    both the declared ``Content-Length`` and the bytes actually read (a lying
    header cannot slip past).

``RateLimitMiddleware``
    Per-client token bucket on state-changing methods, so a loop in a browser tab
    or a hostile script cannot start unbounded runs or brute-force the approval
    token.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

Handler = Callable[[Request], Awaitable[Response]]

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    # This service returns JSON and SSE only; nothing should ever execute.
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardening headers to every response."""

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class BodyLimitMiddleware:
    """Reject request bodies larger than `max_bytes`.

    Implemented as raw ASGI so the limit applies while the body streams in,
    rather than after the whole payload has been buffered.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:  # type: ignore[type-arg]
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await _send_too_large(send, self.max_bytes)
                    return
            except ValueError:
                await _send_too_large(send, self.max_bytes)
                return

        received = 0
        too_large = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received, too_large
            message: dict[str, Any] = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)


async def _send_too_large(send: Callable, limit: int) -> None:  # type: ignore[type-arg]
    body = b'{"detail":"Request body too large.","limit_bytes":' + str(limit).encode() + b"}"
    await send(
        {
            "type": "http.response.start",
            "status": status.HTTP_413_CONTENT_TOO_LARGE,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-client rate limit on state-changing requests."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"}),
        max_clients: int = 4096,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.methods = methods
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}

    def _client_key(self, request: Request) -> str:
        # Trust the platform-provided forwarded header only for its first hop.
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return (request.client.host if request.client else "unknown")[:64]

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if request.method not in self.methods:
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (now - window[0])))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded.", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)
        if len(self._hits) > self.max_clients:
            # Bounded memory: drop the coldest bucket rather than grow forever.
            coldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0.0)
            self._hits.pop(coldest, None)

        return await call_next(request)
