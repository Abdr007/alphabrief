import { RunConsole } from "@/components/RunConsole";
import { DEFAULT_WATCHLIST } from "@/lib/server";

export const dynamic = "force-dynamic";

const CHAIN = [
  { id: "01", name: "SUPERVISOR", detail: "plans · fans out both workers in parallel" },
  { id: "02", name: "MCP TOOLS", detail: "every figure computed by a tool, never a model" },
  { id: "03", name: "WRITER", detail: "cites verified claims · computes nothing" },
  { id: "04", name: "VERIFIER", detail: "recomputes each figure from raw bars — code, not an LLM" },
  { id: "05", name: "HUMAN GATE", detail: "graph pauses · nothing ships unsigned" },
];

export default function ConsolePage() {
  return (
    <div className="space-y-4">
      <section className="panel ticked px-4 py-3.5">
        <p className="text-[10px] uppercase tracking-[0.24em] text-amber-dim">
          governed research terminal · v1.0
        </p>
        <h1 className="mt-1.5 text-[19px] leading-snug tracking-tight text-ink">
          Watch the agents work —{" "}
          <span className="text-amber glow-amber">and watch every number get checked.</span>
        </h1>
        <p className="mt-2 max-w-3xl text-[11.5px] leading-relaxed text-muted">
          A supervisor dispatches a data agent and a news agent in parallel; both consume tools over
          a Model Context Protocol server. A deterministic node then recomputes every figure the
          writer cited, from the raw price bars, before a human is asked to approve anything.
        </p>

        <ol className="mt-3 grid gap-px overflow-hidden border border-rule bg-rule sm:grid-cols-2 lg:grid-cols-5">
          {CHAIN.map((step) => (
            <li key={step.id} className="bg-panel px-2.5 py-2">
              <p className="text-[10px] text-amber-dim">{step.id}</p>
              <p className="mt-0.5 text-[11px] tracking-[0.1em] text-ink">{step.name}</p>
              <p className="mt-1 text-[10px] leading-relaxed text-faint">{step.detail}</p>
            </li>
          ))}
        </ol>
      </section>

      <RunConsole defaultWatchlist={DEFAULT_WATCHLIST} />
    </div>
  );
}
