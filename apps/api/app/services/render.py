"""Render a verified brief to Markdown / HTML.

Claim references are substituted here and nowhere else, so the value a reader
sees is the value the verifier checked. A reference with no matching claim
renders as an explicit ``[unverified]`` marker rather than silently vanishing.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping

from app.models.brief import CLAIM_REF_PATTERN, Brief, NumericClaim

UNVERIFIED_MARKER = "[unverified]"


def format_claim(claim: NumericClaim) -> str:
    """Human-facing rendering of one verified figure."""
    value = claim.value
    if claim.unit == "usd":
        return f"${value:,.2f}"
    if claim.unit == "percent":
        return f"{value:+.2f}%"
    if claim.unit == "ratio":
        return f"{value:,.2f}"
    if claim.unit == "score":
        return f"{value:+.2f}"
    return f"{value}"


def substitute(text: str, claims: Mapping[str, NumericClaim]) -> str:
    """Replace every ``{{cN}}`` with its verified, formatted value."""

    def _replace(match: re.Match[str]) -> str:
        claim = claims.get(match.group(1))
        return format_claim(claim) if claim else UNVERIFIED_MARKER

    return CLAIM_REF_PATTERN.sub(_replace, text or "")


def _cell(claim_id: str | None, claims: Mapping[str, NumericClaim]) -> str:
    if not claim_id:
        return "—"
    claim = claims.get(claim_id)
    return format_claim(claim) if claim else UNVERIFIED_MARKER


def render_markdown(brief: Brief) -> str:
    """The brief as Markdown — used for the email body and the archive."""
    claims = brief.claims_by_id()
    out: list[str] = []
    out.append(f"# AlphaBrief — {brief.generated_for}")
    out.append("")
    out.append(f"**{substitute(brief.headline, claims)}**")
    out.append("")
    if brief.partial:
        out.append("> ⚠️ **Partial coverage** — at least one ticker failed to return data.")
        out.append("")
    out.append(substitute(brief.executive_summary, claims))
    out.append("")

    if brief.snapshot:
        out.append("## Snapshot")
        out.append("")
        out.append(
            "| Ticker | Company | Close | 1D | 30D | Vol (ann.) | Max DD | P/E | Sentiment |"
        )
        out.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in brief.snapshot:
            out.append(
                "| "
                + " | ".join(
                    [
                        row.ticker,
                        row.company or "—",
                        _cell(row.last_close, claims),
                        _cell(row.change_1d, claims),
                        _cell(row.return_30d, claims),
                        _cell(row.volatility, claims),
                        _cell(row.max_drawdown, claims),
                        _cell(row.pe_ratio, claims),
                        _cell(row.sentiment, claims),
                    ]
                )
                + " |"
            )
        out.append("")

    if brief.key_moves:
        out.append("## Key moves")
        out.append("")
        for move in brief.key_moves:
            out.append(f"- **{move.ticker}** — {substitute(move.narrative, claims)}")
        out.append("")

    if brief.news_and_sentiment:
        out.append("## News & sentiment")
        out.append("")
        for entry in brief.news_and_sentiment:
            out.append(f"- **{entry.ticker}** — {substitute(entry.summary, claims)}")
            if entry.top_headline:
                source = f" _({entry.headline_source})_" if entry.headline_source else ""
                out.append(f"  - Top headline: “{entry.top_headline}”{source}")
        out.append("")

    if brief.risk_flags:
        out.append("## Risk flags")
        out.append("")
        for flag in brief.risk_flags:
            out.append(
                f"- **{flag.ticker}** · _{flag.category}_ — {substitute(flag.assessment, claims)}"
            )
            out.append(f"  - Evidence: “{flag.evidence}”")
        out.append("")

    if brief.watch_items:
        out.append("## Watch items")
        out.append("")
        for item in brief.watch_items:
            prefix = f"**{item.ticker}** — " if item.ticker else ""
            out.append(f"- {prefix}{substitute(item.item, claims)}")
        out.append("")

    if brief.data_gaps:
        out.append("## Data gaps")
        out.append("")
        for gap in brief.data_gaps:
            out.append(f"- {gap}")
        out.append("")

    out.append("---")
    out.append(
        "_Every figure above was computed by the MCP tool layer and independently "
        "recomputed by a deterministic verifier before this brief was released. "
        "Delivery required human approval._"
    )
    return "\n".join(out)


def render_html(brief: Brief) -> str:
    """A self-contained dark-theme HTML email body."""
    claims = brief.claims_by_id()
    esc = html.escape

    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(value)}</td>"
            for value in [
                row.ticker,
                row.company or "—",
                _cell(row.last_close, claims),
                _cell(row.change_1d, claims),
                _cell(row.return_30d, claims),
                _cell(row.volatility, claims),
                _cell(row.max_drawdown, claims),
                _cell(row.pe_ratio, claims),
                _cell(row.sentiment, claims),
            ]
        )
        + "</tr>"
        for row in brief.snapshot
    )

    moves = "".join(
        f"<li><strong>{esc(m.ticker)}</strong> — {esc(substitute(m.narrative, claims))}</li>"
        for m in brief.key_moves
    )
    news = "".join(
        f"<li><strong>{esc(n.ticker)}</strong> — {esc(substitute(n.summary, claims))}"
        + (f"<br><em>“{esc(n.top_headline)}”</em>" if n.top_headline else "")
        + "</li>"
        for n in brief.news_and_sentiment
    )
    risks = "".join(
        f"<li><strong>{esc(r.ticker)}</strong> · {esc(r.category)} — "
        f"{esc(substitute(r.assessment, claims))}<br><em>“{esc(r.evidence)}”</em></li>"
        for r in brief.risk_flags
    )
    watch = "".join(
        f"<li>{esc((w.ticker + ' — ') if w.ticker else '')}{esc(substitute(w.item, claims))}</li>"
        for w in brief.watch_items
    )
    gaps = "".join(f"<li>{esc(g)}</li>" for g in brief.data_gaps)

    partial_banner = (
        '<p style="background:#3b1d1d;border-left:3px solid #f87171;padding:10px 14px;'
        'border-radius:6px;">⚠️ <strong>Partial coverage</strong> — at least one ticker '
        "failed to return data.</p>"
        if brief.partial
        else ""
    )

    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#0a0e1a;color:#e2e8f0;
font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;">
  <div style="max-width:760px;margin:0 auto;">
    <p style="color:#22d3ee;letter-spacing:.18em;font-size:12px;margin:0 0 4px;">ALPHABRIEF</p>
    <h1 style="margin:0 0 4px;font-size:24px;">{esc(substitute(brief.headline, claims))}</h1>
    <p style="color:#94a3b8;margin:0 0 20px;font-size:13px;">{esc(brief.generated_for)}</p>
    {partial_banner}
    <p style="line-height:1.65;">{esc(substitute(brief.executive_summary, claims))}</p>

    <h2 style="color:#22d3ee;font-size:15px;margin-top:28px;">Snapshot</h2>
    <table cellspacing="0" cellpadding="8" style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#94a3b8;text-align:left;">
        <th>Ticker</th><th>Company</th><th>Close</th><th>1D</th><th>30D</th>
        <th>Vol</th><th>Max DD</th><th>P/E</th><th>Sent.</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>

    {f'<h2 style="color:#22d3ee;font-size:15px;margin-top:28px;">Key moves</h2><ul style="line-height:1.7;">{moves}</ul>' if moves else ""}
    {f'<h2 style="color:#22d3ee;font-size:15px;margin-top:28px;">News &amp; sentiment</h2><ul style="line-height:1.7;">{news}</ul>' if news else ""}
    {f'<h2 style="color:#f87171;font-size:15px;margin-top:28px;">Risk flags</h2><ul style="line-height:1.7;">{risks}</ul>' if risks else ""}
    {f'<h2 style="color:#22d3ee;font-size:15px;margin-top:28px;">Watch items</h2><ul style="line-height:1.7;">{watch}</ul>' if watch else ""}
    {f'<h2 style="color:#94a3b8;font-size:15px;margin-top:28px;">Data gaps</h2><ul style="line-height:1.7;color:#94a3b8;">{gaps}</ul>' if gaps else ""}

    <hr style="border:none;border-top:1px solid #1e293b;margin:28px 0 12px;">
    <p style="color:#64748b;font-size:12px;line-height:1.6;">
      Every figure above was computed by the MCP tool layer and independently recomputed by a
      deterministic verifier before release. Delivery required human approval.
    </p>
  </div>
</body></html>"""
