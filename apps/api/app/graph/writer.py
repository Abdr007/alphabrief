"""Writer agent — assembles the brief. Computes nothing.

The claim table is minted deterministically from tool-computed metrics *before*
the model is asked for anything, and the model's returned claim list is
overwritten with that canonical table. The model therefore controls only:

* which verified claim each sentence cites, and
* the prose around it.

It cannot alter a value, and the schema rejects prose containing a bare numeral.
Whatever it returns is then recomputed from raw price bars by the verifier.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.core.budget import BudgetExceededError
from app.core.claude import AgentRole, LLMRequest, PromptHint
from app.core.events import EventKind
from app.core.security import harden_untrusted_text
from app.graph.compose import compose_brief
from app.graph.context import RunContext, current_context
from app.graph.llm import call_model
from app.graph.prompts import WRITER_SYSTEM, build_emit_brief_tool
from app.graph.state import RunState
from app.models.brief import Brief
from app.models.run import RunError, RunMode, RunStatus, TokenSpend

logger = logging.getLogger(__name__)

#: How far the injected-fault demo mode shifts a claim, in the claim's own units.
DEMO_MISMATCH_DELTA = 7.77


def data_gaps_from_state(state: RunState) -> list[str]:
    """Human-readable gaps, derived from recorded run errors."""
    gaps: list[str] = []
    seen: set[str] = set()
    for error in state.get("errors", []):
        if error.severity != "error":
            continue
        label = f"{error.ticker or 'run'}: {error.message}"
        if label in seen:
            continue
        seen.add(label)
        gaps.append(label)
    return gaps[:10]


def build_canonical_brief(state: RunState) -> Brief:
    """Mint the verified claim table and a complete deterministic brief."""
    return compose_brief(
        session_date=str(state.get("session_date", "")),
        tickers=list(state.get("tickers", [])),
        metrics=state.get("metrics", {}),
        fundamentals=state.get("fundamentals", {}),
        sentiment=state.get("sentiment", {}),
        news=state.get("news", {}),
        risk_events=list(state.get("risk_events", [])),
        data_gaps=data_gaps_from_state(state),
    )


def _writer_context_payload(state: RunState, canonical: Brief) -> dict[str, Any]:
    """Everything the model may cite, already verified."""
    headlines: dict[str, list[str]] = {}
    for ticker, feed in state.get("news", {}).items():
        headlines[ticker] = [harden_untrusted_text(item.title) for item in feed.items[:6]]
    return {
        "session_date": state.get("session_date"),
        "watchlist": list(state.get("tickers", [])),
        "claims": [claim.model_dump() for claim in canonical.claims],
        "snapshot_tickers": [row.ticker for row in canonical.snapshot],
        "companies": {row.ticker: row.company for row in canonical.snapshot if row.company},
        "sentiment_labels": {
            ticker: value.label for ticker, value in state.get("sentiment", {}).items()
        },
        "risk_events": [event.model_dump() for event in state.get("risk_events", [])],
        "headlines": headlines,
        "data_gaps": canonical.data_gaps,
        "partial": canonical.partial,
    }


async def _ask_model_for_brief(
    ctx: RunContext,
    state: RunState,
    canonical: Brief,
    mismatch_feedback: list[str],
) -> tuple[Brief | None, TokenSpend | None, list[RunError]]:
    """Ask Claude to write the brief; fall back to the canonical one on failure."""
    payload = _writer_context_payload(state, canonical)
    schema = Brief.model_json_schema()
    tool = build_emit_brief_tool(schema)

    instruction = (
        "Write the morning brief using ONLY the verified claims below. Reference a "
        "figure as {{claim_id}} — for example {{c1}}. Do not write any numeral in "
        "narrative prose. Copy quoted headlines character-for-character.\n\n"
        f"VERIFIED STATE:\n{json.dumps(payload, indent=2, default=str)[:24000]}"
    )
    if mismatch_feedback:
        instruction += (
            "\n\nYour previous attempt failed deterministic verification for these "
            "reasons. Fix them exactly — do not restate a figure differently, cite the "
            "correct claim id instead:\n- " + "\n- ".join(mismatch_feedback[:10])
        )

    errors: list[RunError] = []
    outcome = await call_model(
        ctx,
        LLMRequest(
            role=AgentRole.WRITER,
            hint=PromptHint.WRITER_COMPOSE,
            system=WRITER_SYSTEM,
            messages=[{"role": "user", "content": instruction}],
            tools=[tool],
            forced_tool=tool.name,
            max_tokens=ctx.settings.llm_max_tokens,
            context={
                "session_date": state.get("session_date"),
                "tickers": list(state.get("tickers", [])),
                "metrics": state.get("metrics", {}),
                "fundamentals": state.get("fundamentals", {}),
                "sentiment": state.get("sentiment", {}),
                "news": state.get("news", {}),
                "risk_events": list(state.get("risk_events", [])),
                "data_gaps": canonical.data_gaps,
            },
        ),
    )

    raw = outcome.result.first_tool(tool.name)
    if not raw:
        errors.append(
            RunError(
                stage="writer",
                message="writer returned no emit_brief call; using the deterministic assembly",
                severity="warning",
            )
        )
        return None, outcome.spend, errors

    # The model never controls values: the canonical claim table always wins.
    raw["claims"] = [claim.model_dump() for claim in canonical.claims]
    raw.setdefault("generated_for", canonical.generated_for)
    raw.setdefault("watchlist", canonical.watchlist)
    raw["partial"] = canonical.partial

    try:
        return Brief.model_validate(raw), outcome.spend, errors
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
        )
        logger.warning("writer output failed schema validation: %s", detail)
        errors.append(
            RunError(
                stage="writer",
                message=f"writer output rejected by schema ({detail}); "
                "fell back to the deterministic assembly",
                severity="warning",
            )
        )
        return None, outcome.spend, errors


def _inject_demo_mismatch(brief: Brief) -> Brief:
    """Corrupt one claim so the verification screen visibly goes red."""
    if not brief.claims:
        return brief
    claims = [claim.model_copy() for claim in brief.claims]
    target = claims[0]
    claims[0] = target.model_copy(update={"value": target.value + DEMO_MISMATCH_DELTA})
    return brief.model_copy(update={"claims": claims})


async def writer_node(state: RunState) -> dict[str, Any]:
    """Graph node: produce the Pydantic-enforced brief."""
    ctx = current_context()
    previous_report = state.get("verification")
    is_regeneration = previous_report is not None
    regenerations = int(state.get("regenerations", 0)) + (1 if is_regeneration else 0)

    await ctx.emit(
        EventKind.WRITER_STARTED,
        "Regenerating the brief after failed verification"
        if is_regeneration
        else "Assembling the brief from verified state",
        {"regeneration": is_regeneration},
    )

    canonical = build_canonical_brief(state)
    errors: list[RunError] = []
    spend: TokenSpend | None = None
    brief = canonical

    if ctx.engine.name == "anthropic":
        feedback: list[str] = []
        if isinstance(previous_report, dict):
            checks = previous_report.get("claim_checks", [])
            feedback = [
                f"{c.get('claim_id')} ({c.get('ticker')}.{c.get('metric')}): {c.get('detail')}"
                for c in checks
                if isinstance(c, dict) and c.get("status") != "match"
            ]
            feedback += list(previous_report.get("structural_issues", []))
        try:
            with ctx.tracer.step("writer", run_id=ctx.run_id) as span:
                model_brief, spend, model_errors = await _ask_model_for_brief(
                    ctx, state, canonical, feedback
                )
                span.update(output={"used_model_output": model_brief is not None})
            errors.extend(model_errors)
            if model_brief is not None:
                brief = model_brief
        except BudgetExceededError as exc:
            await ctx.emit(EventKind.RUN_FAILED, "Budget exhausted while writing", {})
            return {
                "status": RunStatus.BUDGET_ABORT,
                "abort_reason": str(exc),
                "errors": [RunError(stage="writer", message=str(exc))],
                "regenerations": regenerations,
            }

    if state.get("mode") == RunMode.DEMO_MISMATCH:
        brief = _inject_demo_mismatch(brief)
        errors.append(
            RunError(
                stage="writer",
                message="demo_mismatch mode: a claim value was deliberately corrupted "
                "to exercise the verifier",
                severity="warning",
            )
        )

    await ctx.emit(
        EventKind.WRITER_COMPLETED,
        f"Brief assembled — {len(brief.claims)} claims, {len(brief.snapshot)} snapshot rows",
        {
            "claims": len(brief.claims),
            "snapshot_rows": len(brief.snapshot),
            "partial": brief.partial,
            "headline": brief.headline,
        },
    )

    update: dict[str, Any] = {
        "brief": brief,
        "regenerations": regenerations,
        "errors": errors,
    }
    if spend is not None:
        update["token_spend"] = spend
    return update
