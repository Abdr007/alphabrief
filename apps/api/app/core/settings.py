"""Runtime configuration.

Every secret and tunable is read from the environment — nothing is hard-coded and
nothing is committed. See `.env.example` at the repository root for the full list.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_WATCHLIST: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "TSLA", "AMZN")

#: Model routing by task weight (spec §3 "Models"). Haiku plans, Sonnet works.
SUPERVISOR_MODEL = "claude-haiku-4-5"
WORKER_MODEL = "claude-sonnet-4-6"

#: USD per 1M tokens, (input, output). Source: Anthropic published pricing.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
}

EngineName = Literal["auto", "anthropic", "deterministic"]
McpTransport = Literal["stdio", "inmemory"]


class Settings(BaseSettings):
    """Immutable, env-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # `model_` is a Pydantic-protected namespace; our model_* fields are
        # deliberate configuration, so the protection is disabled explicitly.
        protected_namespaces=(),
    )

    # ---------------------------------------------------------------- app ---
    environment: Literal["local", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_title: str = "AlphaBrief API"

    # -------------------------------------------------------------- claude ---
    anthropic_api_key: str | None = None
    model_supervisor: str = SUPERVISOR_MODEL
    model_worker: str = WORKER_MODEL
    model_writer: str = WORKER_MODEL
    #: "auto" uses Anthropic when a key is present, else the deterministic engine.
    llm_engine: EngineName = "auto"
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    #: Effort hint for Sonnet workers. Haiku 4.5 does not accept `effort`.
    worker_effort: Literal["low", "medium", "high"] = "medium"

    # ------------------------------------------------------------ guardrails ---
    max_iterations: int = 15
    token_budget_usd: float = 0.50
    token_budget_tokens: int = 400_000
    max_watchlist_size: int = 12
    max_regenerations: int = 1

    # ------------------------------------------------------------------ mcp ---
    mcp_transport: McpTransport = "stdio"
    #: Share one in-memory MCP server (and therefore one provider cache) across
    #: runs. Used by the eval harness so 20 runs are polite to free providers.
    mcp_shared_server: bool = False
    mcp_startup_timeout_seconds: float = 30.0
    mcp_call_timeout_seconds: float = 60.0
    price_history_days: int = 120
    news_limit: int = 6
    #: Polite delay between outbound provider calls, per MCP server process.
    provider_min_interval_seconds: float = 0.20

    # ------------------------------------------------------------ persistence ---
    database_url: str = "sqlite:///./alphabrief.db"
    db_echo: bool = False

    # ---------------------------------------------------------- observability ---
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # ------------------------------------------------------------------ smtp ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_timeout_seconds: float = 20.0

    # ------------------------------------------------------------- security ---
    #: Bearer token required by the approval endpoints. Auto-generated when unset
    #: so that a deployment can never accidentally ship a well-known default.
    approval_token: str | None = None
    cors_allow_origins: str = "http://localhost:3000"
    #: Hard ceiling on any single inbound request body (bytes).
    max_request_bytes: int = 64 * 1024

    # ---------------------------------------------------------------- runs ---
    default_watchlist: str = ",".join(DEFAULT_WATCHLIST)
    max_events_per_run: int = 4000
    run_timeout_seconds: float = 900.0

    @field_validator("cors_allow_origins", "default_watchlist")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @property
    def watchlist(self) -> list[str]:
        return [t.strip().upper() for t in self.default_watchlist.split(",") if t.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.smtp_to)

    @property
    def resolved_engine(self) -> Literal["anthropic", "deterministic"]:
        """Which LLM engine this process will actually use."""
        if self.llm_engine == "anthropic":
            return "anthropic"
        if self.llm_engine == "deterministic":
            return "deterministic"
        return "anthropic" if self.anthropic_api_key else "deterministic"

    def require_approval_token(self) -> str:
        """Return the approval bearer token, generating one on first use.

        There is deliberately no default value: an unset token yields a fresh
        random one per process rather than a well-known constant an attacker
        could guess against a deployment that forgot to configure it.
        """
        token = self.approval_token
        if not token:
            token = secrets.token_urlsafe(32)
            self.approval_token = token
        return token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def price_of(model: str) -> tuple[float, float]:
    """USD per 1M (input, output) tokens for `model`.

    Unknown models fall back to the most expensive known tier so that a
    mis-configured model can never silently under-report spend to the budget guard.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    return max(MODEL_PRICING.values(), key=lambda p: p[1])
