"use client";

import type { ClaimCheck, QuoteCheck } from "@/lib/types";

/**
 * The convergence lattice.
 *
 * Every figure in the brief is plotted twice: once as the writer claimed it,
 * once as `recompute.py` independently re-derived it from the raw bars using a
 * deliberately different implementation (Welford against two-pass, `bisect`
 * against a linear scan). Agreement shows up as *coincidence* — the two markers
 * land on top of each other — rather than as a green tick you have to trust.
 *
 * Each track is normalised to its own tolerance rather than to its own
 * magnitude, so the violet band is the same width on every row. That makes a
 * volatility check and a dollar-close check readable against each other: inside
 * the band is verified, outside is a finding.
 */

/** Half-width of the tolerance band, as a percentage of the track. */
const BAND = 12;

function formatValue(value: number | null | undefined, unit: string): string {
  // Defensive against a shape the API might grow later: a demo that crashes is
  // worse than a demo that renders an em dash.
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  switch (unit) {
    case "usd":
      return `$${value.toFixed(2)}`;
    case "percent":
      return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
    default:
      return value.toFixed(2);
  }
}

/**
 * The gap between the two computations.
 *
 * A difference that rounds away at the displayed precision is shown as an exact
 * zero rather than as "-0.00%", which reads like a defect in the verifier when
 * it is in fact the verifier agreeing.
 */
function formatDelta(delta: number | null, unit: string): string {
  if (delta === null) return "—";
  const rendered = formatValue(delta, unit);
  return /^[+-]?[$]?0(\.0+)?%?$/.test(rendered) ? "Δ 0" : `Δ ${rendered}`;
}

/**
 * Where the recomputed value sits relative to the claim.
 *
 * The claim is always dead centre because it is the assertion under test; the
 * recompute is what moves. Clamped so a wild divergence stays on screen instead
 * of disappearing off the end of the track.
 */
function offsetPercent(check: ClaimCheck): number {
  if (typeof check.recomputed !== "number" || typeof check.claimed !== "number") return 50;
  const tolerance = check.tolerance > 0 ? check.tolerance : 1e-9;
  const delta = check.recomputed - check.claimed;
  const position = 50 + (delta / tolerance) * BAND;
  if (!Number.isFinite(position)) return 50;
  return Math.min(97, Math.max(3, position));
}

export interface LatticeSummary {
  ok: boolean;
  matched: number;
  checked: number;
  coverage: number;
}

export function VerificationLattice({
  claims,
  quotes,
  summary,
  activeClaim,
  onActiveClaim,
}: {
  claims: ClaimCheck[];
  quotes: QuoteCheck[];
  summary: LatticeSummary | null;
  activeClaim: string | null;
  onActiveClaim: (claimId: string | null) => void;
}) {
  if (claims.length === 0 && quotes.length === 0 && !summary) return null;

  const mismatched = claims.filter((check) => check.status === "mismatch").length;

  return (
    <section className="panel px-4 py-4">
      <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="hdr flex-1">
          <span>Convergence lattice</span>
        </span>
        {summary ? (
          <span className="text-[10px] uppercase tracking-[0.16em]">
            <span className={mismatched > 0 ? "text-coral" : "text-violet"}>
              {summary.matched}/{summary.checked} agreed
            </span>
            <span className="mx-2 text-edge-hot">·</span>
            <span className="text-faint">{Math.round(summary.coverage * 100)}% coverage</span>
          </span>
        ) : null}
      </div>

      <p className="mb-4 max-w-3xl text-[11px] leading-relaxed text-muted">
        Each figure the writer cited, re-derived from the raw price bars by a second, deliberately
        different implementation. The band is the tolerance the recompute had to land inside.
      </p>

      {claims.length > 0 ? (
        <>
          <Legend />
          <ul className="mt-3 space-y-1.5">
            {claims.map((check, index) => (
              // Composite key: a regeneration re-emits the same claim ids, and a
              // duplicate key silently drops rows.
              <Track
                key={`${check.claim_id}-${index}`}
                check={check}
                active={activeClaim === check.claim_id}
                onActiveClaim={onActiveClaim}
              />
            ))}
          </ul>
        </>
      ) : (
        <p className="text-[11px] text-faint">Waiting for the verifier to report its first claim.</p>
      )}

      {quotes.length > 0 ? (
        <div className="mt-5 border-t border-edge pt-4">
          <p className="eyebrow">Quoted text</p>
          <p className="mt-1.5 mb-3 max-w-3xl text-[11px] leading-relaxed text-muted">
            Headlines the brief quotes are matched against what the news tool actually returned, so
            a quotation cannot be paraphrased into something the source never said.
          </p>
          <ul className="space-y-1.5">
            {quotes.map((quote, index) => (
              <li
                key={`${quote.ticker}-${index}`}
                className="flex items-start gap-3 text-[11px] leading-relaxed"
              >
                <span className="w-[62px] shrink-0 text-ink">{quote.ticker}</span>
                <span
                  className={`shrink-0 ${quote.status === "match" ? "text-violet" : "text-coral"}`}
                >
                  {quote.status === "match" ? "verbatim" : "altered"}
                </span>
                <span className="min-w-0 flex-1 truncate text-faint">{quote.text}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {mismatched > 0 ? (
        <p className="mt-4 rounded-[3px] border border-coral-dim bg-coral/10 px-3 py-2 text-[11px] leading-relaxed text-coral">
          {mismatched} figure{mismatched === 1 ? "" : "s"} did not survive recomputation. The writer
          gets exactly one regeneration; if the gap persists the run is routed to human review
          rather than delivered.
        </p>
      ) : null}
    </section>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-t border-edge pt-3 text-[10px] text-faint">
      <span className="flex items-center gap-1.5">
        <span className="marker marker-claimed !relative !top-0 !ml-0 !transform-none" aria-hidden />
        <span>as written</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span
          className="marker marker-recomputed !relative !top-0 !ml-0 !transform-none"
          aria-hidden
        />
        <span>independently recomputed</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-3 w-6 bg-violet/16 ring-1 ring-violet/40" aria-hidden />
        <span>tolerance</span>
      </span>
    </div>
  );
}

function Track({
  check,
  active,
  onActiveClaim,
}: {
  check: ClaimCheck;
  active: boolean;
  onActiveClaim: (claimId: string | null) => void;
}) {
  const diverged = check.status === "mismatch";
  const unverifiable = check.status === "unverifiable";
  const position = offsetPercent(check);
  const gapLeft = Math.min(50, position);
  const gapWidth = Math.abs(position - 50);

  const delta =
    typeof check.recomputed === "number" && typeof check.claimed === "number"
      ? check.recomputed - check.claimed
      : null;

  return (
    <li
      className={`flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[3px] px-2 py-1 transition-colors ${
        active ? "bg-violet/10" : ""
      }`}
      onMouseEnter={() => onActiveClaim(check.claim_id)}
      onMouseLeave={() => onActiveClaim(null)}
    >
      {/* identity */}
      <div className="w-[186px] shrink-0">
        <span className="text-[11px] text-ink">{check.ticker}</span>
        <span className="mx-1.5 text-edge-hot">/</span>
        <span className="text-[11px] text-muted">{check.metric}</span>
        <span className="ml-1.5 text-[9px] text-faint">{check.claim_id}</span>
      </div>

      {/* the track — fixed width, so the tolerance band means the same thing on
          every row no matter how wide the panel gets */}
      <div className="track w-[240px] shrink-0" role="img" aria-label={trackLabel(check)}>
        <span
          className="tolerance"
          style={{ left: `${50 - BAND}%`, width: `${BAND * 2}%` }}
          aria-hidden
        />
        {diverged ? (
          <span
            className="divergence"
            style={{ left: `${gapLeft}%`, width: `${gapWidth}%` }}
            aria-hidden
          />
        ) : null}
        <span className="marker marker-claimed" style={{ left: "50%" }} aria-hidden />
        {check.recomputed !== null ? (
          <span
            className={`marker marker-recomputed ${diverged ? "marker-diverged" : ""}`}
            style={{ left: `${position}%` }}
            aria-hidden
          />
        ) : null}
      </div>

      {/* how far the recompute landed from the claim */}
      <div className={`w-[112px] shrink-0 text-[10px] ${diverged ? "text-coral" : "text-faint"}`}>
        {formatDelta(delta, check.unit)}
      </div>

      {/* readout */}
      <div className="flex-1 text-right text-[11px]">
        {unverifiable ? (
          <span className="text-faint">no source data</span>
        ) : diverged ? (
          <>
            <span className="text-live">{formatValue(check.claimed, check.unit)}</span>
            <span className="mx-1 text-coral">≠</span>
            <span className="text-coral">{formatValue(check.recomputed, check.unit)}</span>
          </>
        ) : (
          <>
            <span className="text-live">{formatValue(check.claimed, check.unit)}</span>
            <span className="ml-2 text-[10px] text-violet">agreed</span>
          </>
        )}
      </div>
    </li>
  );
}

function trackLabel(check: ClaimCheck): string {
  if (check.status === "unverifiable") {
    return `${check.ticker} ${check.metric}: could not be recomputed, no source data`;
  }
  if (check.status === "mismatch") {
    return `${check.ticker} ${check.metric}: written as ${formatValue(
      check.claimed,
      check.unit,
    )} but recomputed as ${formatValue(check.recomputed, check.unit)} — outside tolerance`;
  }
  return `${check.ticker} ${check.metric}: written as ${formatValue(
    check.claimed,
    check.unit,
  )} and recomputed to the same value within tolerance`;
}
