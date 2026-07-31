import { RunConsole } from "@/components/RunConsole";
import { DEFAULT_WATCHLIST } from "@/lib/server";

export const dynamic = "force-dynamic";

/**
 * Three constraints, not three steps — the orchestration field below already
 * shows the order. What is worth stating up front is what the system is
 * forbidden from doing, because that is where the guarantee comes from.
 */
const CONSTRAINTS = [
  {
    rule: "The writer cannot type a number",
    detail:
      "The brief schema rejects any narrative containing a bare numeral. A figure can only enter as a reference into the claim table.",
  },
  {
    rule: "Every figure is recomputed by different code",
    detail:
      "A deterministic node re-derives each claim from the raw bars — Welford against two-pass, bisect against a linear scan — so agreement is not self-confirmation.",
  },
  {
    rule: "Nothing is delivered unsigned",
    detail:
      "The graph suspends at a checkpointed interrupt. It survives a restart, and only an authenticated decision resumes it.",
  },
];

export default function ConsolePage() {
  return (
    <div className="space-y-4">
      <section className="panel px-6 py-7 sm:px-8 sm:py-9">
        <p className="eyebrow">Multi-agent research orchestration</p>

        <h1 className="display mt-3 max-w-[24ch] text-[30px] font-extrabold leading-[1.08] text-ink sm:text-[42px]">
          Agents that cannot
          <br />
          make a number up.
        </h1>

        <p className="prose mt-5 max-w-[62ch] text-[15px] leading-[1.7] text-muted">
          A supervisor fans a data agent and a news agent out in parallel; both reach the market only
          through a Model Context Protocol tool server. The writer that assembles the brief is
          forbidden from typing a numeral — every figure arrives as a reference like{" "}
          <span className="cite">+2.14%</span> into a table of tool-computed claims, and a second,
          deliberately different implementation re-derives each one before anyone is asked to sign.
        </p>

        <ul className="mt-7 grid gap-px overflow-hidden rounded-[3px] border border-edge bg-edge sm:grid-cols-3">
          {CONSTRAINTS.map((constraint) => (
            <li key={constraint.rule} className="bg-chassis px-4 py-4">
              <p className="display text-[14px] font-semibold leading-snug text-ink">
                {constraint.rule}
              </p>
              <p className="prose mt-2 text-[12.5px] leading-[1.55] text-muted">
                {constraint.detail}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <RunConsole defaultWatchlist={DEFAULT_WATCHLIST} />
    </div>
  );
}
