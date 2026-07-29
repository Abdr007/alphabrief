"use client";

import type { ClaimCheck } from "@/lib/types";

/**
 * The verification ledger: every figure the brief states, beside the figure a
 * second implementation derived from the raw price bars, and the delta between
 * them. MATCH in phosphor, FAIL in alert. This is the screen that wins the room.
 */
export function VerificationPanel({
  claims,
  summary,
}: {
  claims: ClaimCheck[];
  summary: { ok: boolean; matched: number; checked: number; coverage: number } | null;
}) {
  if (claims.length === 0 && !summary) return null;
  const failed = claims.filter((claim) => claim.status !== "match").length;

  return (
    <section className="panel ticked">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 rule-b px-3 py-2">
        <span className="hdr min-w-[220px] flex-1">
          <span>Deterministic verification</span>
        </span>
        {summary ? (
          <div className="flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]">
            <span className="text-faint">
              coverage<span className="mx-1 text-rule-hot">:</span>
              <span className="text-ink">{(summary.coverage * 100).toFixed(0)}%</span>
            </span>
            <span
              className={`border px-2 py-0.5 ${
                summary.ok
                  ? "border-phosphor-dim bg-phosphor/10 text-phosphor glow-phos"
                  : "border-alert-dim bg-alert/10 text-alert"
              }`}
            >
              {summary.matched}/{summary.checked} verified
            </span>
          </div>
        ) : null}
      </header>

      <p className="rule-b px-3 py-1.5 text-[11px] text-faint">
        Each figure recomputed from raw price bars by an independent implementation — not by the
        model that stated it.
      </p>

      <div className="max-h-[380px] overflow-y-auto">
        <table className="w-full border-collapse text-[11.5px]">
          <thead className="sticky top-0 z-10 bg-panel">
            <tr className="label rule-b text-left">
              <th className="px-3 py-1.5 font-normal">id</th>
              <th className="px-2 py-1.5 font-normal">sym</th>
              <th className="px-2 py-1.5 font-normal">metric</th>
              <th className="px-2 py-1.5 text-right font-normal">stated</th>
              <th className="px-2 py-1.5 text-right font-normal">recomputed</th>
              <th className="px-2 py-1.5 text-right font-normal">delta</th>
              <th className="px-3 py-1.5 text-right font-normal">result</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim, index) => {
              const ok = claim.status === "match";
              return (
                <tr
                  key={`${claim.claim_id}-${claim.ticker}-${claim.metric}-${index}`}
                  className={`tick-in border-b border-rule/50 ${
                    ok ? "hover:bg-raised" : "bg-alert/6"
                  }`}
                  style={{ animationDelay: `${Math.min(index * 28, 560)}ms` }}
                >
                  <td className="px-3 py-1.5 text-faint">{claim.claim_id}</td>
                  <td className="px-2 py-1.5 text-ink">{claim.ticker}</td>
                  <td className="px-2 py-1.5 text-muted">{prettyMetric(claim.metric)}</td>
                  <td className="px-2 py-1.5 text-right text-ink">{fmt(claim.claimed)}</td>
                  <td className="px-2 py-1.5 text-right text-muted">
                    {claim.recomputed === null ? "—" : fmt(claim.recomputed)}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right ${ok ? "text-faint" : "text-alert"}`}
                  >
                    {claim.delta === null ? "—" : claim.delta.toExponential(1)}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {ok ? (
                      <span className="text-phosphor glow-phos">✓ MATCH</span>
                    ) : (
                      <span className="text-alert">
                        ✕ {claim.status === "mismatch" ? "MISMATCH" : "UNVERIFIED"}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {summary && !summary.ok ? (
        <p className="border-t border-alert-dim bg-alert/10 px-3 py-2 text-[11px] text-alert">
          ▲ {failed} claim{failed === 1 ? "" : "s"} failed verification. The brief was regenerated
          once, failed again, and was held for human review — it was never delivered.
        </p>
      ) : null}
    </section>
  );
}

function fmt(value: number): string {
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toFixed(Math.abs(value) < 1 ? 4 : 2);
}

function prettyMetric(metric: string): string {
  return metric.replace(/_pct$/, "").replace(/_/g, " ").replace("annualised", "ann");
}
