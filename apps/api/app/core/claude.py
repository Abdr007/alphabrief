"""Claude model router + a deterministic offline engine.

Model routing by task weight (spec §3): Haiku 4.5 plans and routes, Sonnet 4.6
does the worker and writer reasoning. Cost discipline is stated as architecture,
not as a comment.

Two engines implement the same `LLMEngine` protocol:

``AnthropicEngine``
    Real Claude calls. Used whenever ``ANTHROPIC_API_KEY`` is present.

``DeterministicEngine``
    A rule-based stand-in that drives the **identical graph, the identical MCP
    tools and the identical deterministic verifier**. It exists so the quality
    gates (pytest, the 20-run eval, CI) run at ``$0`` and without network access
    to Anthropic. It never invents market data: every number still comes from the
    MCP tools, which still call yfinance and the real RSS feeds. What it replaces
    is only the *decision-making* an LLM would otherwise contribute.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, cast

from app.core.budget import Usage
from app.core.settings import Settings, get_settings


class AgentRole(StrEnum):
    """Which routed model handles a call."""

    SUPERVISOR = "supervisor"
    DATA = "data_agent"
    NEWS = "news_agent"
    WRITER = "writer"


class PromptHint(StrEnum):
    """Opaque tag identifying *which* decision is being asked for.

    Ignored entirely by :class:`AnthropicEngine`; used by
    :class:`DeterministicEngine` to dispatch to the matching rule.
    """

    SUPERVISOR_PLAN = "supervisor.plan"
    DATA_ANALYSE = "data_agent.analyse"
    NEWS_ASSESS = "news_agent.assess"
    WRITER_COMPOSE = "writer.compose"


class LLMRefusalError(RuntimeError):
    """The model declined the request (``stop_reason == "refusal"``)."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool advertised to the model."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One model call."""

    role: AgentRole
    hint: PromptHint
    system: str
    messages: list[dict[str, Any]]
    tools: Sequence[ToolSpec] = ()
    forced_tool: str | None = None
    max_tokens: int | None = None
    #: Structured data also rendered into `messages`. The Anthropic engine never
    #: reads it; the deterministic engine uses it instead of parsing prose.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMResult:
    """One model response."""

    model: str
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: str
    engine: str

    def first_tool(self, name: str) -> dict[str, Any] | None:
        for call in self.tool_calls:
            if call.name == name:
                return call.arguments
        return None


class LLMEngine(Protocol):
    """Everything the graph needs from a language model."""

    name: str

    def model_for(self, role: AgentRole) -> str:
        """The model id this engine would use for `role`."""
        ...

    async def complete(self, request: LLMRequest) -> LLMResult:
        """Execute one model call."""
        ...


# --------------------------------------------------------------------------- #
# Anthropic engine                                                            #
# --------------------------------------------------------------------------- #


class AnthropicEngine:
    """Real Claude calls through the official SDK."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None) -> None:
        import anthropic  # imported lazily so the package is optional offline

        self._settings = settings or get_settings()
        if not self._settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the anthropic engine")
        self._client = anthropic.AsyncAnthropic(
            api_key=self._settings.anthropic_api_key,
            timeout=self._settings.llm_timeout_seconds,
            max_retries=self._settings.llm_max_retries,
        )

    def model_for(self, role: AgentRole) -> str:
        cfg = self._settings
        if role is AgentRole.SUPERVISOR:
            return cfg.model_supervisor
        if role is AgentRole.WRITER:
            return cfg.model_writer
        return cfg.model_worker

    async def complete(self, request: LLMRequest) -> LLMResult:
        model = self.model_for(request.role)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens or self._settings.llm_max_tokens,
            "system": request.system,
            "messages": request.messages,
        }
        if request.tools:
            kwargs["tools"] = [t.to_anthropic() for t in request.tools]
            if request.forced_tool:
                kwargs["tool_choice"] = {"type": "tool", "name": request.forced_tool}

        # Haiku 4.5 rejects `effort`; Sonnet 4.6 supports it. Adaptive thinking is
        # enabled for the reasoning workers but not for the writer, whose call
        # uses a forced tool choice and needs no exploration.
        if request.role is not AgentRole.SUPERVISOR:
            kwargs["output_config"] = {"effort": self._settings.worker_effort}
            if request.role in (AgentRole.DATA, AgentRole.NEWS):
                kwargs["thinking"] = {"type": "adaptive"}

        response = await self._client.messages.create(**kwargs)

        stop_reason = str(response.stop_reason or "end_turn")
        if stop_reason == "refusal":
            raise LLMRefusalError(f"{model} declined the request")

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        arguments=cast(dict[str, Any], getattr(block, "input", {}) or {}),
                    )
                )

        raw_usage = response.usage
        usage = Usage(
            input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(raw_usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(raw_usage, "cache_creation_input_tokens", 0) or 0),
        )
        return LLMResult(
            model=model,
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            engine=self.name,
        )


# --------------------------------------------------------------------------- #
# Deterministic engine                                                         #
# --------------------------------------------------------------------------- #

#: Transparent sentiment lexicon. Applied to *real* headline text pulled from the
#: live RSS feeds — this is a baseline scorer, not fabricated sentiment.
_POSITIVE_TERMS: frozenset[str] = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surges",
        "record",
        "upgrade",
        "upgraded",
        "rally",
        "gain",
        "gains",
        "growth",
        "profit",
        "strong",
        "soar",
        "soars",
        "jump",
        "jumps",
        "outperform",
        "raises",
        "raise",
        "expands",
        "wins",
        "approval",
        "breakthrough",
        "bullish",
        "rebound",
        "optimism",
        "milestone",
    }
)
_NEGATIVE_TERMS: frozenset[str] = frozenset(
    {
        "miss",
        "misses",
        "plunge",
        "plunges",
        "slump",
        "downgrade",
        "downgraded",
        "loss",
        "losses",
        "weak",
        "fall",
        "falls",
        "drop",
        "drops",
        "lawsuit",
        "probe",
        "investigation",
        "recall",
        "layoff",
        "layoffs",
        "cuts",
        "cut",
        "warning",
        "warns",
        "bearish",
        "decline",
        "declines",
        "halt",
        "fine",
        "penalty",
        "delay",
        "delays",
        "risk",
        "slowdown",
        "selloff",
    }
)
#: Headline patterns that constitute a flagged risk event.
_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(lawsuit|sued|litigation|class action)\b", "legal"),
    (r"\b(probe|investigation|subpoena|antitrust|regulator\w*)\b", "regulatory"),
    (r"\b(recall|defect|safety)\b", "product"),
    (r"\b(layoff\w*|job cuts|restructur\w*)\b", "restructuring"),
    (r"\b(guidance cut|lowers guidance|profit warning|downgrade[ds]?)\b", "guidance"),
    (r"\b(breach|hack|ransomware|outage)\b", "security"),
    (r"\b(short seller|fraud|accounting)\b", "integrity"),
)


def _tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def score_headlines(headlines: Sequence[str]) -> tuple[float, str]:
    """Lexicon sentiment over real headlines → (score in [-1, 1], reasoning).

    The reasoning string is deliberately **numeral-free**: it is interpolated into
    brief narrative, where the schema forbids bare digits (every figure must be a
    verified claim reference). Counts are therefore described qualitatively and
    the score itself is carried as a claim.
    """
    if not headlines:
        return 0.0, "No headlines were retrieved, so sentiment defaults to neutral."
    positive = 0
    negative = 0
    hits: list[str] = []
    for headline in headlines:
        for token in _tokenise(headline):
            if token in _POSITIVE_TERMS:
                positive += 1
                hits.append(token)
            elif token in _NEGATIVE_TERMS:
                negative += 1
                hits.append(token)
    total = positive + negative
    if total == 0:
        return 0.0, "The retrieved headlines carried no directional language."
    score = round((positive - negative) / total, 3)
    if positive > negative:
        lead = "Positive language outweighed negative"
    elif negative > positive:
        lead = "Negative language outweighed positive"
    else:
        lead = "Positive and negative language were evenly balanced"
    sample = ", ".join(dict.fromkeys(hits[:6]))
    return score, f"{lead} across the retrieved headlines ({sample})."


def extract_risk_events(ticker: str, headlines: Sequence[str]) -> list[dict[str, str]]:
    """Flag risk headlines by pattern match over real headline text."""
    events: list[dict[str, str]] = []
    for headline in headlines:
        lowered = headline.lower()
        for pattern, category in _RISK_PATTERNS:
            if re.search(pattern, lowered):
                events.append(
                    {"ticker": ticker, "category": category, "headline": headline.strip()}
                )
                break
    return events


class DeterministicEngine:
    """Rule-based stand-in for Claude used by CI, the eval harness and demos.

    Produces the same structured tool calls the real agents would, so the graph,
    the MCP tool layer and the verifier are all exercised identically.
    """

    name = "deterministic"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._counter = 0

    def model_for(self, role: AgentRole) -> str:
        cfg = self._settings
        if role is AgentRole.SUPERVISOR:
            return f"{cfg.model_supervisor}#deterministic"
        if role is AgentRole.WRITER:
            return f"{cfg.model_writer}#deterministic"
        return f"{cfg.model_worker}#deterministic"

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:04d}"

    async def complete(self, request: LLMRequest) -> LLMResult:
        handlers = {
            PromptHint.SUPERVISOR_PLAN: self._plan,
            PromptHint.DATA_ANALYSE: self._analyse,
            PromptHint.NEWS_ASSESS: self._assess,
            PromptHint.WRITER_COMPOSE: self._compose,
        }
        text, calls = handlers[request.hint](request)
        # Deterministic engine costs nothing; usage is reported as zero so the
        # budget guard and cost readout never over-state real spend.
        return LLMResult(
            model=self.model_for(request.role),
            text=text,
            tool_calls=calls,
            usage=Usage(),
            stop_reason="end_turn",
            engine=self.name,
        )

    # ------------------------------------------------------------- handlers ---
    def _plan(self, request: LLMRequest) -> tuple[str, list[ToolCall]]:
        pending: list[str] = list(request.context.get("pending_tickers", []))
        plan = {
            "dispatch": ["data_agent", "news_agent"] if pending else [],
            "tickers": pending,
            "reason": (
                f"{len(pending)} ticker(s) still missing data — dispatching both "
                "workers in parallel."
                if pending
                else "All tickers complete — routing to the writer."
            ),
        }
        return json.dumps(plan), []

    def _analyse(self, request: LLMRequest) -> tuple[str, list[ToolCall]]:
        tickers: list[str] = list(request.context.get("tickers", []))
        days = int(request.context.get("days", self._settings.price_history_days))
        calls = [
            ToolCall(
                id=self._next_id("data"),
                name="plan_market_data",
                arguments={"tickers": tickers, "days": days},
            )
        ]
        return (
            f"Fetching {days}d price history, fundamentals and computed metrics "
            f"for {', '.join(tickers) or 'no tickers'} via the MCP tool server."
        ), calls

    def _assess(self, request: LLMRequest) -> tuple[str, list[ToolCall]]:
        per_ticker: dict[str, list[str]] = request.context.get("headlines", {})
        assessments: list[dict[str, Any]] = []
        for ticker, headlines in per_ticker.items():
            score, reasoning = score_headlines(headlines)
            assessments.append(
                {
                    "ticker": ticker,
                    "score": score,
                    "reasoning": reasoning,
                    "risk_events": extract_risk_events(ticker, headlines),
                }
            )
        calls = [
            ToolCall(
                id=self._next_id("news"),
                name="emit_sentiment",
                arguments={"assessments": assessments},
            )
        ]
        return f"Scored {len(assessments)} ticker(s) from live RSS headlines.", calls

    def _compose(self, request: LLMRequest) -> tuple[str, list[ToolCall]]:
        from app.graph.compose import compose_brief_payload

        payload = compose_brief_payload(request.context)
        calls = [
            ToolCall(
                id=self._next_id("brief"),
                name="emit_brief",
                arguments=payload,
            )
        ]
        return "Assembled the brief from verified state — no arithmetic performed.", calls


# --------------------------------------------------------------------------- #
# Factory                                                                      #
# --------------------------------------------------------------------------- #


def build_engine(settings: Settings | None = None) -> LLMEngine:
    """Return the engine this process should use."""
    cfg = settings or get_settings()
    if cfg.resolved_engine == "anthropic":
        return AnthropicEngine(cfg)
    return DeterministicEngine(cfg)
