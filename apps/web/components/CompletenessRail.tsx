"use client";

/** Per-ticker state completeness, drawn as terminal block meters. */
export function CompletenessRail({
  tickers,
  completeness,
}: {
  tickers: string[];
  completeness: Record<string, number>;
}) {
  if (tickers.length === 0) return null;
  const done = Object.values(completeness).filter((value) => value >= 1).length;

  return (
    <section className="panel px-3 py-2.5">
      <div className="mb-2 flex items-center gap-3">
        <span className="hdr flex-1">
          <span>State completeness</span>
        </span>
        <span className="text-[10px] uppercase tracking-[0.14em] text-faint">
          <span className="text-ink">{done}</span>/{tickers.length} complete
        </span>
      </div>

      <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 xl:grid-cols-3">
        {tickers.map((ticker) => {
          const value = completeness[ticker] ?? 0;
          const pct = Math.round(value * 100);
          const complete = value >= 1;
          return (
            <div key={ticker} className="flex items-center gap-2.5 text-[11.5px]">
              <span className="w-[74px] shrink-0 truncate text-ink">{ticker}</span>
              <span
                className={`flex-1 tracking-[-0.06em] ${
                  complete ? "text-phosphor glow-phos" : "text-amber"
                }`}
                aria-hidden="true"
              >
                {meter(value)}
              </span>
              <span
                className={`w-9 shrink-0 text-right ${
                  complete ? "text-phosphor" : "text-faint"
                }`}
              >
                {pct}%
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-2 text-[10px] text-faint">
        market data 60% · news &amp; sentiment 40%
      </p>
    </section>
  );
}

/** 20-cell block meter — reads instantly, no layout thrash. */
function meter(value: number): string {
  const cells = 20;
  const filled = Math.round(Math.min(1, Math.max(0, value)) * cells);
  return "█".repeat(filled) + "░".repeat(cells - filled);
}
