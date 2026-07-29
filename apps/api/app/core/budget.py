"""Per-run token and spend budget guard.

Loop safety has two independent brakes (spec §7 "Loop safety"):

1. the supervisor's hard iteration cap, and
2. this guard, which aborts the run with status ``BUDGET_ABORT`` the moment
   cumulative token or USD spend crosses the configured ceiling.

The guard is charged *before* a model call is issued when the cost is already
known to exceed the remaining budget, and *after* every call with actual usage,
so a single runaway response cannot blow past the ceiling unnoticed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import Settings, get_settings, price_of


class BudgetExceededError(RuntimeError):
    """Raised when a run exhausts its token or USD budget."""

    def __init__(self, spent_usd: float, spent_tokens: int, limit_usd: float, limit_tokens: int):
        self.spent_usd = spent_usd
        self.spent_tokens = spent_tokens
        self.limit_usd = limit_usd
        self.limit_tokens = limit_tokens
        super().__init__(
            f"run budget exhausted: ${spent_usd:.4f}/${limit_usd:.4f} "
            f"and {spent_tokens}/{limit_tokens} tokens"
        )


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage from one model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def cost_usd(self, model: str) -> float:
        """Blended USD cost of this call for `model`."""
        per_m_in, per_m_out = price_of(model)
        cost = (self.input_tokens / 1_000_000) * per_m_in
        cost += (self.output_tokens / 1_000_000) * per_m_out
        # Cache reads bill at ~0.1x input; cache writes at ~1.25x input.
        cost += (self.cache_read_tokens / 1_000_000) * per_m_in * 0.10
        cost += (self.cache_write_tokens / 1_000_000) * per_m_in * 1.25
        return cost


@dataclass(slots=True)
class BudgetSnapshot:
    """Serialisable view of a guard, surfaced in telemetry and the archive."""

    spent_usd: float
    spent_tokens: int
    limit_usd: float
    limit_tokens: int
    calls: int
    per_model: dict[str, int] = field(default_factory=dict)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spent_usd": round(self.spent_usd, 6),
            "spent_tokens": self.spent_tokens,
            "limit_usd": self.limit_usd,
            "limit_tokens": self.limit_tokens,
            "calls": self.calls,
            "remaining_usd": round(self.remaining_usd, 6),
            "per_model_tokens": dict(self.per_model),
        }


class BudgetGuard:
    """Thread-safe cumulative spend tracker for a single run."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._limit_usd = cfg.token_budget_usd
        self._limit_tokens = cfg.token_budget_tokens
        self._spent_usd = 0.0
        self._spent_tokens = 0
        self._calls = 0
        self._per_model: dict[str, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api ---
    def charge(self, model: str, usage: Usage) -> BudgetSnapshot:
        """Record a completed model call, then raise if the run is now over budget."""
        with self._lock:
            self._spent_usd += usage.cost_usd(model)
            self._spent_tokens += usage.total_tokens
            self._calls += 1
            self._per_model[model] = self._per_model.get(model, 0) + usage.total_tokens
            snapshot = self._snapshot_locked()
        if snapshot.spent_usd > self._limit_usd or snapshot.spent_tokens > self._limit_tokens:
            raise BudgetExceededError(
                snapshot.spent_usd,
                snapshot.spent_tokens,
                self._limit_usd,
                self._limit_tokens,
            )
        return snapshot

    def ensure_headroom(self, model: str, projected_output_tokens: int) -> None:
        """Pre-flight check: refuse a call whose worst case cannot fit the budget."""
        _, per_m_out = price_of(model)
        projected = (projected_output_tokens / 1_000_000) * per_m_out
        with self._lock:
            would_spend = self._spent_usd + projected
            would_tokens = self._spent_tokens + projected_output_tokens
            spent_usd, spent_tokens = self._spent_usd, self._spent_tokens
        if would_spend > self._limit_usd or would_tokens > self._limit_tokens:
            raise BudgetExceededError(spent_usd, spent_tokens, self._limit_usd, self._limit_tokens)

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return self._snapshot_locked()

    # -------------------------------------------------------------- internal ---
    def _snapshot_locked(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            spent_usd=self._spent_usd,
            spent_tokens=self._spent_tokens,
            limit_usd=self._limit_usd,
            limit_tokens=self._limit_tokens,
            calls=self._calls,
            per_model=dict(self._per_model),
        )
