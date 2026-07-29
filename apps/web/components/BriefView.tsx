"use client";

import { cell, claimIndex, signOf, substituteClaims } from "@/lib/format";
import type { Brief } from "@/lib/types";

/** The brief itself, rendered as a terminal tape with every claim substituted. */
export function BriefView({ brief }: { brief: Brief }) {
  const claims = claimIndex(brief);

  return (
    <article className="panel ticked">
      <header className="rule-b px-3 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="hdr min-w-[200px] flex-1">
            <span>Morning brief</span>
          </span>
          <span className="text-[10px] uppercase tracking-[0.16em] text-faint">
            session<span className="mx-1 text-rule-hot">:</span>
            <span className="text-ink">{brief.generated_for}</span>
          </span>
        </div>

        <h2 className="mt-2 text-[17px] leading-snug text-amber glow-amber">
          {substituteClaims(brief.headline, claims)}
        </h2>

        {brief.partial ? (
          <p className="mt-2 border border-alert-dim bg-alert/8 px-2.5 py-1.5 text-[11px] text-alert">
            ▲ PARTIAL COVERAGE — at least one symbol failed to return data and is excluded from the
            snapshot. Gaps are listed below rather than papered over.
          </p>
        ) : null}

        <p className="mt-2 max-w-4xl text-[12px] leading-relaxed text-muted">
          {substituteClaims(brief.executive_summary, claims)}
        </p>
      </header>

      {brief.snapshot.length > 0 ? (
        <div className="overflow-x-auto rule-b">
          <table className="w-full min-w-[820px] border-collapse text-[11.5px]">
            <thead>
              <tr className="label rule-b text-left">
                <th className="px-3 py-1.5 font-normal">sym</th>
                <th className="px-2 py-1.5 font-normal">name</th>
                <th className="px-2 py-1.5 text-right font-normal">close</th>
                <th className="px-2 py-1.5 text-right font-normal">1d</th>
                <th className="px-2 py-1.5 text-right font-normal">30d</th>
                <th className="px-2 py-1.5 text-right font-normal">vol ann</th>
                <th className="px-2 py-1.5 text-right font-normal">max dd</th>
                <th className="px-2 py-1.5 text-right font-normal">p/e</th>
                <th className="px-3 py-1.5 text-right font-normal">sent</th>
              </tr>
            </thead>
            <tbody>
              {brief.snapshot.map((row) => (
                <tr key={row.ticker} className="border-b border-rule/50 hover:bg-raised">
                  <td className="px-3 py-2 text-amber">{row.ticker}</td>
                  <td className="px-2 py-2 text-muted">{row.company ?? "—"}</td>
                  <td className="px-2 py-2 text-right text-ink">{cell(row.last_close, claims)}</td>
                  <td className={`px-2 py-2 text-right ${tone(signOf(row.change_1d, claims))}`}>
                    {cell(row.change_1d, claims)}
                  </td>
                  <td className={`px-2 py-2 text-right ${tone(signOf(row.return_30d, claims))}`}>
                    {cell(row.return_30d, claims)}
                  </td>
                  <td className="px-2 py-2 text-right text-muted">
                    {cell(row.volatility, claims)}
                  </td>
                  <td className="px-2 py-2 text-right text-alert/80">
                    {cell(row.max_drawdown, claims)}
                  </td>
                  <td className="px-2 py-2 text-right text-muted">{cell(row.pe_ratio, claims)}</td>
                  <td className={`px-3 py-2 text-right ${tone(signOf(row.sentiment, claims))}`}>
                    {cell(row.sentiment, claims)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="grid gap-px bg-rule md:grid-cols-2">
        {brief.key_moves.length > 0 ? (
          <Block title="Key moves">
            {brief.key_moves.map((move, index) => (
              <p key={`${move.ticker}-${index}`} className="text-[11.5px] leading-relaxed">
                <span className="mr-2 text-amber">
                  {move.direction === "up" ? "▲" : move.direction === "down" ? "▼" : "▪"}{" "}
                  {move.ticker}
                </span>
                <span className="text-muted">{substituteClaims(move.narrative, claims)}</span>
              </p>
            ))}
          </Block>
        ) : null}

        {brief.news_and_sentiment.length > 0 ? (
          <Block title="News & sentiment">
            {brief.news_and_sentiment.map((entry) => (
              <div key={entry.ticker} className="text-[11.5px] leading-relaxed">
                <span className="mr-2 text-amber">{entry.ticker}</span>
                <span className="text-muted">{substituteClaims(entry.summary, claims)}</span>
                {entry.top_headline ? (
                  <p className="mt-1 border-l border-rule-hot pl-2.5 text-[11px] text-faint">
                    “{entry.top_headline}”
                    {entry.headline_source ? <span> — {entry.headline_source}</span> : null}
                  </p>
                ) : null}
              </div>
            ))}
          </Block>
        ) : null}

        {brief.risk_flags.length > 0 ? (
          <Block title="Risk flags" tone="alert">
            {brief.risk_flags.map((flag, index) => (
              <div key={`${flag.ticker}-${index}`} className="text-[11.5px] leading-relaxed">
                <span className="mr-2 text-alert">▲ {flag.ticker}</span>
                <span className="mr-2 border border-alert-dim px-1.5 text-[10px] uppercase text-alert/80">
                  {flag.category}
                </span>
                <span className="text-muted">{substituteClaims(flag.assessment, claims)}</span>
                <p className="mt-1 border-l border-alert-dim pl-2.5 text-[11px] text-faint">
                  “{flag.evidence}”
                </p>
              </div>
            ))}
          </Block>
        ) : null}

        {brief.watch_items.length > 0 ? (
          <Block title="Watch items">
            {brief.watch_items.map((item, index) => (
              <p key={index} className="text-[11.5px] leading-relaxed text-muted">
                <span className="mr-2 text-rule-hot">▸</span>
                {item.ticker ? <span className="mr-2 text-amber">{item.ticker}</span> : null}
                {substituteClaims(item.item, claims)}
              </p>
            ))}
          </Block>
        ) : null}

        {brief.data_gaps.length > 0 ? (
          <Block title="Data gaps" tone="alert">
            {brief.data_gaps.map((gap, index) => (
              <p key={index} className="text-[11px] text-faint">
                <span className="mr-2 text-alert-dim">✕</span>
                {gap}
              </p>
            ))}
          </Block>
        ) : null}
      </div>

      <footer className="border-t border-rule px-3 py-2 text-[10px] uppercase tracking-[0.14em] text-faint">
        {brief.claims.length} figures · each tool-computed, independently recomputed, human-approved
      </footer>
    </article>
  );
}

function Block({
  title,
  children,
  tone = "amber",
}: {
  title: string;
  children: React.ReactNode;
  tone?: "amber" | "alert";
}) {
  return (
    <section className="bg-panel px-3 py-3">
      <p
        className={`label mb-2 ${tone === "alert" ? "text-alert/80" : "text-amber/80"}`}
      >
        {title}
      </p>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

function tone(sign: number): string {
  if (sign > 0) return "text-phosphor";
  if (sign < 0) return "text-alert";
  return "text-muted";
}
