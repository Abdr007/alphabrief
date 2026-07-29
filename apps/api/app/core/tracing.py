"""Langfuse tracing for every model call and every graph step.

Tracing is strictly optional: with no Langfuse keys configured the tracer is a
no-op that costs nothing and never raises. This keeps "I debug agents by reading
traces, not by guessing" true in production without making local development or
CI depend on a third-party service.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Protocol, Self

from app.core.budget import Usage
from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

ObservationType = str


class _Observation(Protocol):
    """The subset of a Langfuse observation this module relies on."""

    def update(self, **kwargs: Any) -> Any: ...
    def end(self, **kwargs: Any) -> Any: ...


class _NullSpan:
    """No-op observation used when tracing is disabled."""

    def update(self, **kwargs: Any) -> None:  # noqa: ARG002 - protocol shape
        return None

    def end(self, **kwargs: Any) -> None:  # noqa: ARG002 - protocol shape
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class Tracer:
    """Thin, failure-tolerant wrapper over the Langfuse client."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None
        if not self._settings.langfuse_enabled:
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=self._settings.langfuse_public_key,
                secret_key=self._settings.langfuse_secret_key,
                host=self._settings.langfuse_host,
                environment=self._settings.environment,
            )
        except Exception:
            logger.warning("Langfuse initialisation failed; continuing untraced", exc_info=True)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------ api ---
    @contextmanager
    def step(
        self,
        name: str,
        *,
        run_id: str,
        as_type: ObservationType = "span",
        input_data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[_Observation]:
        """Trace one graph step. Yields an observation you can `.update(output=...)`."""
        if self._client is None:
            yield _NullSpan()
            return
        span: Any = None
        try:
            span = self._client.start_observation(
                name=name,
                as_type=as_type,
                input=input_data,
                metadata={"run_id": run_id, **(metadata or {})},
            )
        except Exception:
            logger.debug("langfuse start_observation failed", exc_info=True)
            yield _NullSpan()
            return
        try:
            yield span
        except Exception as exc:
            self._safe(span.update, level="ERROR", status_message=str(exc)[:500])
            self._safe(span.end)
            raise
        else:
            self._safe(span.end)

    def record_generation(
        self,
        name: str,
        *,
        run_id: str,
        model: str,
        input_data: Any,
        output_data: Any,
        usage: Usage,
        cost_usd: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a completed model call with token spend and cost."""
        if self._client is None:
            return
        try:
            generation = self._client.start_observation(
                name=name,
                as_type="generation",
                model=model,
                input=input_data,
                metadata={"run_id": run_id, **(metadata or {})},
            )
        except Exception:
            logger.debug("langfuse generation failed", exc_info=True)
            return
        self._safe(
            generation.update,
            output=output_data,
            usage_details={
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "cache_read_input_tokens": usage.cache_read_tokens,
                "cache_creation_input_tokens": usage.cache_write_tokens,
                "total": usage.total_tokens,
            },
            cost_details={"total": cost_usd},
        )
        self._safe(generation.end)

    def flush(self) -> None:
        if self._client is None:
            return
        self._safe(self._client.flush)

    def shutdown(self) -> None:
        if self._client is None:
            return
        self._safe(self._client.shutdown)
        self._client = None

    # -------------------------------------------------------------- internal ---
    @staticmethod
    def _safe(fn: Any, **kwargs: Any) -> None:
        try:
            fn(**kwargs)
        except Exception:
            logger.debug("langfuse call failed", exc_info=True)


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Process-wide tracer singleton."""
    global _tracer  # noqa: PLW0603 - deliberate process-wide singleton
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


def reset_tracer() -> None:
    """Test hook."""
    global _tracer  # noqa: PLW0603 - deliberate process-wide singleton
    if _tracer is not None:
        _tracer.shutdown()
    _tracer = None
