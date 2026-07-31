"use client";

import { Citation, renderWithCitations } from "@/components/Citation";
import { claimIndex, signOf } from "@/lib/format";
import type { Brief, NumericClaim } from "@/lib/types";

/**
 * The brief, rendered as a document.
 *
 * It is the only light surface in the product, and that is the argument: the
 * chassis is apparatus, this is the artifact a person is being asked to sign.
 * Every figure appears as a citation chip rather than as text, because in this
 * system a number is a reference into a verified claim table — never something
 * the writer typed.
 */
export function BriefView({
  brief,
  activeClaim,
  onActiveClaim,
}: {
  brief: Brief;
  activeClaim: string | null;
  onActiveClaim: (claimId: string | null) => void;
}) {
  const claims = claimIndex(brief);
  const prose = (text: string) => renderWithCitations(text, claims, activeClaim, onActiveClaim);

  return (
    <article className="doc overflow-hidden">
      <header className="border-b border-doc-rule px-6 py-6 sm:px-9 sm:py-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-doc-muted">
            AlphaBrief · morning brief
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-doc-muted">
            {brief.generated_for}
          </p>
        </div>

        <h2 className="mt-4 max-w-4xl text-[26px] font-extrabold leading-[1.15] tracking-[-0.03em] text-doc-ink sm:text-[32px]">
          {prose(brief.headline)}
        </h2>

        {brief.partial ? (
          <p className="mt-4 rounded-[2px] border-l-2 border-doc-neg bg-doc-neg/8 px-3 py-2 font-mono text-[11px] leading-relaxed text-doc-neg">
            Partial coverage — at least one symbol returned no data and is excluded from the
            snapshot. The gaps are listed rather than papered over.
          </p>
        ) : null}

        <p className="mt-4 max-w-3xl text-[15px] leading-[1.65] text-doc-ink/85">
          {prose(brief.executive_summary)}
        </p>
      </header>

      {brief.snapshot.length > 0 ? (
        <div className="thin-scroll overflow-x-auto border-b border-doc-rule">
          <table className="w-full min-w-[860px] border-collapse font-mono text-[12px]">
            <thead>
              <tr className="border-b border-doc-rule text-left">
                {["symbol", "name", "close", "1d", "30d", "vol ann", "max dd", "p/e", "sentiment"].map(
                  (heading, index) => (
                    <th
                      key={heading}
                      className={`px-3 py-2 text-[9px] font-normal uppercase tracking-[0.16em] text-doc-muted ${
                        index >= 2 ? "text-right" : ""
                      }`}
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {brief.snapshot.map((row) => (
                <tr key={row.ticker} className="border-b border-doc-rule/60 last:border-0">
                  <td className="px-3 py-2.5 font-bold text-doc-ink">{row.ticker}</td>
                  <td className="max-w-[220px] truncate px-3 py-2.5 text-doc-muted">
                    {row.company ?? "—"}
                  </td>
                  <Cell id={row.last_close} claims={claims} {...{ activeClaim, onActiveClaim }} />
                  <Cell
                    id={row.change_1d}
                    claims={claims}
                    tone={signOf(row.change_1d, claims)}
                    {...{ activeClaim, onActiveClaim }}
                  />
                  <Cell
                    id={row.return_30d}
                    claims={claims}
                    tone={signOf(row.return_30d, claims)}
                    {...{ activeClaim, onActiveClaim }}
                  />
                  <Cell id={row.volatility} claims={claims} {...{ activeClaim, onActiveClaim }} />
                  <Cell id={row.max_drawdown} claims={claims} {...{ activeClaim, onActiveClaim }} />
                  <Cell id={row.pe_ratio} claims={claims} {...{ activeClaim, onActiveClaim }} />
                  <Cell
                    id={row.sentiment}
                    claims={claims}
                    tone={signOf(row.sentiment, claims)}
                    {...{ activeClaim, onActiveClaim }}
                  />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* Hairlines come from the cells, not from a background showing through a
          gap: an odd number of sections would leave that background exposed as a
          grey block where a cell should be. */}
      <div className="grid sm:grid-cols-2">
        {brief.key_moves.length > 0 ? (
          <Block title="Key moves">
            {brief.key_moves.map((move, index) => (
              <p key={`${move.ticker}-${index}`}>
                <Ticker>{move.ticker}</Ticker>
                <span className="text-doc-ink/80">{prose(move.narrative)}</span>
              </p>
            ))}
          </Block>
        ) : null}

        {brief.news_and_sentiment.length > 0 ? (
          <Block title="News &amp; sentiment">
            {brief.news_and_sentiment.map((entry) => (
              <div key={entry.ticker}>
                <Ticker>{entry.ticker}</Ticker>
                <span className="text-doc-ink/80">{prose(entry.summary)}</span>
                {entry.top_headline ? (
                  <p className="mt-1.5 border-l-2 border-doc-rule pl-3 text-[13px] italic leading-relaxed text-doc-muted">
                    “{entry.top_headline}”
                    {entry.headline_source ? (
                      <span className="font-mono text-[10px] not-italic"> — {entry.headline_source}</span>
                    ) : null}
                  </p>
                ) : null}
              </div>
            ))}
          </Block>
        ) : null}

        {brief.risk_flags.length > 0 ? (
          <Block title="Risk flags" tone="warn">
            {brief.risk_flags.map((flag, index) => (
              <div key={`${flag.ticker}-${index}`}>
                <Ticker tone="warn">{flag.ticker}</Ticker>
                <span className="mr-2 rounded-[2px] border border-doc-neg/40 px-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-doc-neg">
                  {flag.category}
                </span>
                <span className="text-doc-ink/80">{prose(flag.assessment)}</span>
                <p className="mt-1.5 border-l-2 border-doc-neg/30 pl-3 text-[13px] italic leading-relaxed text-doc-muted">
                  “{flag.evidence}”
                </p>
              </div>
            ))}
          </Block>
        ) : null}

        {brief.watch_items.length > 0 ? (
          <Block title="Watch items">
            {brief.watch_items.map((item, index) => (
              <p key={index} className="text-doc-ink/80">
                {item.ticker ? <Ticker>{item.ticker}</Ticker> : null}
                {prose(item.item)}
              </p>
            ))}
          </Block>
        ) : null}

        {brief.data_gaps.length > 0 ? (
          <Block title="Data gaps" tone="warn">
            {brief.data_gaps.map((gap, index) => (
              <p key={index} className="font-mono text-[12px] leading-relaxed text-doc-muted">
                {gap}
              </p>
            ))}
          </Block>
        ) : null}
      </div>

      <footer className="border-t border-doc-rule px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-doc-muted sm:px-9">
        {brief.claims.length} figures · each computed by a tool, independently recomputed, then
        signed by a human
      </footer>
    </article>
  );
}

function Cell({
  id,
  claims,
  tone = 0,
  activeClaim,
  onActiveClaim,
}: {
  id: string | null;
  claims: Map<string, NumericClaim>;
  tone?: number;
  activeClaim: string | null;
  onActiveClaim: (claimId: string | null) => void;
}) {
  const colour = tone > 0 ? "text-doc-pos" : tone < 0 ? "text-doc-neg" : "text-doc-ink";
  return (
    <td className={`px-3 py-2.5 text-right ${colour}`}>
      {id ? (
        <Citation
          claimId={id}
          claim={claims.get(id)}
          active={activeClaim === id}
          onActiveClaim={onActiveClaim}
        />
      ) : (
        <span className="text-doc-muted">—</span>
      )}
    </td>
  );
}

function Ticker({ children, tone }: { children: React.ReactNode; tone?: "warn" }) {
  return (
    <span
      className={`mr-2 font-mono text-[11px] font-bold ${
        tone === "warn" ? "text-doc-neg" : "text-doc-ink"
      }`}
    >
      {children}
    </span>
  );
}

function Block({
  title,
  children,
  tone,
}: {
  title: string;
  children: React.ReactNode;
  tone?: "warn";
}) {
  return (
    <section className="border-b border-doc-rule bg-porcelain px-6 py-5 sm:px-9 sm:[&:nth-child(odd)]:border-r">
      <h3
        className={`mb-3 font-mono text-[9px] uppercase tracking-[0.22em] ${
          tone === "warn" ? "text-doc-neg" : "text-doc-muted"
        }`}
      >
        {title}
      </h3>
      <div className="space-y-3 text-[14px] leading-[1.6]">{children}</div>
    </section>
  );
}
