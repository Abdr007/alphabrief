"""The schema-level anti-hallucination guarantee, and how a brief renders.

The claim: a figure cannot enter the brief as prose. It must be a `{{cN}}`
reference to a tool-computed value. These tests hold the schema to that.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.graph.compose import compose_brief
from app.graph.state import RunState
from app.models.brief import (
    Brief,
    KeyMove,
    NumericClaim,
    WatchItem,
    extract_claim_refs,
    strip_claim_refs,
    validate_narrative,
)
from app.services.render import (
    UNVERIFIED_MARKER,
    format_claim,
    render_html,
    render_markdown,
    substitute,
)


def _brief(state: RunState) -> Brief:
    return compose_brief(
        session_date=str(state["session_date"]),
        tickers=list(state["tickers"]),
        metrics=state["metrics"],
        fundamentals=state["fundamentals"],
        sentiment=state["sentiment"],
        news=state["news"],
        risk_events=[],
        data_gaps=[],
    )


class TestNarrativeDigitRule:
    @pytest.mark.parametrize(
        "text",
        [
            "The stock rose 5 percent",
            "Closed at 340.28",
            "Up 12%",
            "Revenue of $1B",
            "Q3 results",
        ],
    )
    def test_bare_numerals_are_rejected(self, text: str) -> None:
        with pytest.raises(ValueError, match="bare numerals"):
            validate_narrative(text, "field")

    @pytest.mark.parametrize(
        "text",
        [
            "The stock rose {{c1}} on the session",
            "Closed at {{c12}} after a {{c13}} move",
            "Coverage reads positive with no figures at all",
            "Over the trailing thirty-day window it advanced",
        ],
    )
    def test_claim_references_are_accepted(self, text: str) -> None:
        assert validate_narrative(text, "field") == text

    def test_claim_ids_themselves_do_not_count_as_digits(self) -> None:
        """`{{c999}}` contains digits, but the whole token is stripped first."""
        residue = strip_claim_refs("value {{c999}} here")
        assert not any(char.isdigit() for char in residue)
        assert residue.split() == ["value", "here"]
        assert validate_narrative("value {{c999}} here", "field")

    def test_extract_claim_refs_preserves_order(self) -> None:
        assert extract_claim_refs("{{c3}} then {{c1}} then {{c3}}") == ["c3", "c1", "c3"]

    def test_key_move_narrative_is_validated(self) -> None:
        with pytest.raises(ValidationError):
            KeyMove(ticker="AAPL", narrative="Rose 4 percent", direction="up")

    def test_watch_item_is_validated(self) -> None:
        with pytest.raises(ValidationError):
            WatchItem(ticker="AAPL", item="Watch the 200 day average")

    def test_brief_headline_is_validated(self) -> None:
        with pytest.raises(ValidationError):
            Brief(
                generated_for="2026-01-02",
                watchlist=["AAPL"],
                headline="AAPL up 4 percent",
                executive_summary="Fine.",
            )


class TestClaimIntegrity:
    def test_claim_id_format_is_enforced(self) -> None:
        with pytest.raises(ValidationError):
            NumericClaim(
                claim_id="not-a-claim", ticker="AAPL", metric="last_close", value=1.0, unit="usd"
            )

    def test_unknown_metric_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NumericClaim(claim_id="c1", ticker="AAPL", metric="made_up", value=1.0, unit="usd")

    def test_ticker_is_normalised(self) -> None:
        claim = NumericClaim(
            claim_id="c1", ticker=" aapl ", metric="last_close", value=1.0, unit="usd"
        )
        assert claim.ticker == "AAPL"

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            NumericClaim(
                claim_id="c1",
                ticker="AAPL",
                metric="last_close",
                value=1.0,
                unit="usd",
                smuggled="payload",  # type: ignore[call-arg]
            )

    def test_every_reference_in_a_real_brief_resolves(self, live_state: RunState) -> None:
        brief = _brief(live_state)
        defined = set(brief.claims_by_id())
        assert brief.referenced_claim_ids() <= defined


class TestRendering:
    def test_units_render_correctly(self) -> None:
        assert (
            format_claim(
                NumericClaim(
                    claim_id="c1", ticker="A", metric="last_close", value=340.2, unit="usd"
                )
            )
            == "$340.20"
        )
        assert (
            format_claim(
                NumericClaim(
                    claim_id="c2", ticker="A", metric="change_1d_pct", value=1.5, unit="percent"
                )
            )
            == "+1.50%"
        )
        assert (
            format_claim(
                NumericClaim(claim_id="c3", ticker="A", metric="pe_ratio", value=23.5, unit="ratio")
            )
            == "23.50"
        )

    def test_substitution_uses_verified_values(self, live_state: RunState) -> None:
        brief = _brief(live_state)
        claims = brief.claims_by_id()
        rendered = substitute(brief.key_moves[0].narrative, claims) if brief.key_moves else ""
        assert "{{" not in rendered

    def test_unknown_reference_renders_as_unverified(self) -> None:
        assert substitute("value {{c404}}", {}) == f"value {UNVERIFIED_MARKER}"

    def test_markdown_contains_the_governance_statement(self, live_state: RunState) -> None:
        markdown = render_markdown(_brief(live_state))
        assert markdown.startswith("# AlphaBrief")
        assert "independently" in markdown
        assert "human approval" in markdown
        assert "{{" not in markdown

    def test_html_escapes_third_party_text(self, live_state: RunState) -> None:
        brief = _brief(live_state)
        blocks = [b.model_copy() for b in brief.news_and_sentiment]
        if blocks:
            blocks[0] = blocks[0].model_copy(
                update={"top_headline": "<script>alert('xss')</script>"}
            )
            brief = brief.model_copy(update={"news_and_sentiment": blocks})
        html = render_html(brief)
        assert "<script>alert(" not in html
        assert "&lt;script&gt;" in html or not blocks

    def test_partial_briefs_carry_a_visible_banner(self, live_state: RunState) -> None:
        brief = _brief(live_state).model_copy(update={"partial": True})
        assert "Partial coverage" in render_markdown(brief)
        assert "Partial coverage" in render_html(brief)
