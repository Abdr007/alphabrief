import { ApiError, callApi } from "@/lib/server";
import type { RunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ArchivePage() {
  let runs: RunSummary[] = [];
  let error: string | null = null;
  try {
    runs = await callApi<RunSummary[]>("/v1/runs?limit=60");
  } catch (caught) {
    error = caught instanceof ApiError ? caught.message : "Could not load the archive.";
  }

  const delivered = runs.filter((run) => run.status === "DELIVERED").length;
  const verified = runs.filter((run) => run.verified).length;
  const totalCost = runs.reduce((sum, run) => sum + run.cost_usd, 0);
  const avgLatency = runs.length
    ? runs.reduce((sum, run) => sum + run.latency_ms, 0) / runs.length / 1000
    : 0;

  return (
    <div className="space-y-4">
      <section className="panel ticked px-4 py-3.5">
        <p className="text-[10px] uppercase tracking-[0.24em] text-amber-dim">audit trail</p>
        <h1 className="mt-1.5 text-[19px] tracking-tight text-ink">
          Every run, <span className="text-amber glow-amber">fully audited</span>
        </h1>
        <p className="mt-2 max-w-3xl text-[11.5px] leading-relaxed text-muted">
          Cost, latency, iteration count and verification outcome per brief. Approvals and
          rejections are recorded alongside the run, so the governance chain is reconstructable
          after the fact.
        </p>
      </section>

      {error ? (
        <p className="border border-alert-dim bg-alert/10 px-3 py-2 text-[11.5px] text-alert">
          {error}
        </p>
      ) : null}

      {runs.length > 0 ? (
        <section className="grid gap-px border border-rule bg-rule sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="runs" value={String(runs.length)} />
          <Metric label="delivered" value={String(delivered)} tone="phosphor" />
          <Metric label="verified clean" value={`${verified}/${runs.length}`} tone="amber" />
          <Metric label="model spend" value={`$${totalCost.toFixed(4)}`} />
        </section>
      ) : null}

      <section className="panel overflow-hidden">
        <header className="rule-b px-3 py-2">
          <span className="hdr">
            <span>Run ledger</span>
          </span>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-[11.5px]">
            <thead>
              <tr className="label rule-b text-left">
                <th className="px-3 py-1.5 font-normal">run</th>
                <th className="px-2 py-1.5 font-normal">watchlist</th>
                <th className="px-2 py-1.5 font-normal">headline</th>
                <th className="px-2 py-1.5 font-normal">status</th>
                <th className="px-2 py-1.5 text-right font-normal">iter</th>
                <th className="px-2 py-1.5 text-right font-normal">tools</th>
                <th className="px-2 py-1.5 text-right font-normal">latency</th>
                <th className="px-3 py-1.5 text-right font-normal">cost</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-rule/50 hover:bg-raised">
                  <td className="px-3 py-2 text-faint">
                    {run.id.replace("run_", "").slice(0, 8)}
                  </td>
                  <td className="px-2 py-2 text-amber">{run.tickers.join(" ")}</td>
                  <td className="max-w-[300px] truncate px-2 py-2 text-muted">
                    {run.headline ?? "—"}
                    {run.partial ? (
                      <span className="ml-2 border border-alert-dim px-1 text-[10px] text-alert">
                        partial
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2 py-2">
                    <StatusCell status={run.status} verified={run.verified} />
                  </td>
                  <td className="px-2 py-2 text-right text-muted">{run.iterations}</td>
                  <td className="px-2 py-2 text-right text-muted">{run.tool_calls}</td>
                  <td className="px-2 py-2 text-right text-muted">
                    {(run.latency_ms / 1000).toFixed(1)}s
                  </td>
                  <td className="px-3 py-2 text-right text-muted">${run.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
              {runs.length === 0 && !error ? (
                <tr>
                  <td colSpan={8} className="px-3 py-12 text-center text-faint">
                    no runs recorded — start one from the console
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {runs.length > 0 ? (
        <p className="text-[10px] uppercase tracking-[0.14em] text-faint">
          mean latency<span className="mx-1 text-rule-hot">:</span>
          <span className="text-muted">{avgLatency.toFixed(2)}s per brief</span>
        </p>
      ) : null}
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "ink",
}: {
  label: string;
  value: string;
  tone?: "ink" | "phosphor" | "amber";
}) {
  const tones = { ink: "text-ink", phosphor: "text-phosphor", amber: "text-amber" } as const;
  return (
    <div className="bg-panel px-3 py-2.5">
      <p className="label">{label}</p>
      <p className={`mt-1 text-[17px] ${tones[tone]}`}>{value}</p>
    </div>
  );
}

function StatusCell({ status, verified }: { status: string; verified: boolean }) {
  const tone =
    status === "DELIVERED"
      ? "border-phosphor-dim text-phosphor"
      : status === "REJECTED" || status.includes("ABORT") || status === "FAILED"
        ? "border-alert-dim text-alert"
        : status === "HUMAN_REVIEW"
          ? "border-alert-dim text-alert"
          : "border-rule text-amber";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`border px-1.5 py-0.5 text-[10px] ${tone}`}>{status}</span>
      {verified ? <span className="text-[10px] text-phosphor">✓</span> : null}
    </span>
  );
}
