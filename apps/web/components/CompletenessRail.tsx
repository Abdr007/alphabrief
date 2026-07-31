"use client";

/**
 * Per-ticker state completeness — the signal the supervisor actually routes on.
 *
 * It re-plans until every symbol has both halves of its state, so this is the
 * loop condition made visible rather than a decorative progress bar.
 */
export function CompletenessRail({
  tickers,
  completeness,
}: {
  tickers: string[];
  completeness: Record<string, number>;
}) {
  if (tickers.length === 0) return null;
  const done = tickers.filter((ticker) => (completeness[ticker] ?? 0) >= 1).length;

  return (
    <section className="panel px-4 py-3.5">
      <div className="mb-3 flex items-center gap-3">
        <span className="hdr flex-1">
          <span>Coverage</span>
        </span>
        <span className="text-[10px] uppercase tracking-[0.14em] text-faint">
          <span className={done === tickers.length ? "text-violet" : "text-live"}>{done}</span>
          <span className="mx-1">/</span>
          {tickers.length} complete
        </span>
      </div>

      <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
        {tickers.map((ticker) => {
          const value = Math.min(1, Math.max(0, completeness[ticker] ?? 0));
          const complete = value >= 1;
          return (
            <div key={ticker} className="flex items-center gap-3 text-[11px]">
              <span className="w-[62px] shrink-0 truncate text-ink">{ticker}</span>
              <span
                className="h-[3px] flex-1 overflow-hidden rounded-full bg-edge"
                role="img"
                aria-label={`${ticker} ${Math.round(value * 100)} percent complete`}
              >
                <span
                  className={`block h-full rounded-full transition-[width] duration-500 ${
                    complete ? "bg-violet" : "bg-live"
                  }`}
                  style={{ width: `${value * 100}%` }}
                />
              </span>
              <span className={`w-9 shrink-0 text-right ${complete ? "text-violet" : "text-faint"}`}>
                {Math.round(value * 100)}%
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-[10px] text-faint">
        Market data counts for 60% of a symbol&apos;s state, news and sentiment for 40%.
      </p>
    </section>
  );
}
