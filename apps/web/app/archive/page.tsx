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
      <section className="panel px-6 py-7 sm:px-8">
        <p className="eyebrow">Audit trail</p>
        <h1 className="display mt-3 text-[28px] font-extrabold leading-tight text-ink sm:text-[34px]">
          Every run, reconstructable
        </h1>
        <p className="mt-4 max-w-2xl text-[12.5px] leading-relaxed text-muted">
          Cost, latency, supervisor rounds and verification outcome for each brief. Approvals and
          rejections are stored alongside the run, so the governance chain can be replayed after the
          fact rather than taken on trust.
        </p>
      </section>

      {error ? (
        <p className="rounded-[2px] border border-coral-dim bg-coral/10 px-4 py-2.5 text-[11.5px] text-coral">
          {error}
        </p>
      ) : null}

      {runs.length > 0 ? (
        <section className="grid gap-px overflow-hidden rounded-[3px] border border-edge bg-edge sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Runs" value={String(runs.length)} />
          <Metric label="Delivered" value={String(delivered)} tone="violet" />
          <Metric label="Verified clean" value={`${verified}/${runs.length}`} tone="violet" />
          <Metric label="Model spend" value={`$${totalCost.toFixed(4)}`} />
        </section>
      ) : null}

      <section className="panel overflow-hidden">
        <header className="border-b border-edge px-4 py-2.5">
          <span className="hdr">
            <span>Ledger</span>
          </span>
        </header>
        <div className="thin-scroll overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-[11.5px]">
            <thead>
              <tr className="border-b border-edge text-left">
                {[
                  "run",
                  "watchlist",
                  "headline",
                  "status",
                  "rounds",
                  "tools",
                  "latency",
                  "cost",
                ].map((heading, index) => (
                  <th
                    key={heading}
                    className={`px-3 py-2 text-[9px] font-normal uppercase tracking-[0.18em] text-faint ${
                      index >= 4 ? "text-right" : ""
                    }`}
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-edge/50 hover:bg-raised">
                  <td className="px-3 py-2.5 text-faint">
                    {run.id.replace("run_", "").slice(0, 8)}
                  </td>
                  <td className="px-3 py-2.5 text-ink">{run.tickers.join(" ")}</td>
                  <td className="max-w-[300px] truncate px-3 py-2.5 text-muted">
                    {run.headline ?? "—"}
                    {run.partial ? (
                      <span className="ml-2 rounded-[2px] border border-coral-dim px-1 text-[9px] uppercase text-coral">
                        partial
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusCell status={run.status} verified={run.verified} />
                  </td>
                  <td className="px-3 py-2.5 text-right text-muted">{run.iterations}</td>
                  <td className="px-3 py-2.5 text-right text-muted">{run.tool_calls}</td>
                  <td className="px-3 py-2.5 text-right text-muted">
                    {(run.latency_ms / 1000).toFixed(1)}s
                  </td>
                  <td className="px-3 py-2.5 text-right text-muted">${run.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
              {runs.length === 0 && !error ? (
                <tr>
                  <td colSpan={8} className="px-3 py-16 text-center text-faint">
                    Nothing archived yet. Start a run from the console.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {runs.length > 0 ? (
        <p className="text-[10px] uppercase tracking-[0.14em] text-faint">
          mean latency
          <span className="mx-1.5 text-edge-hot">/</span>
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
  tone?: "ink" | "violet";
}) {
  return (
    <div className="bg-chassis px-4 py-3.5">
      <p className="eyebrow">{label}</p>
      <p className={`mt-1.5 text-[20px] ${tone === "violet" ? "text-violet" : "text-ink"}`}>
        {value}
      </p>
    </div>
  );
}

function StatusCell({ status, verified }: { status: string; verified: boolean }) {
  const tone =
    status === "DELIVERED"
      ? "border-violet-dim text-violet"
      : status === "REJECTED" ||
          status.includes("ABORT") ||
          status === "FAILED" ||
          status === "HUMAN_REVIEW"
        ? "border-coral-dim text-coral"
        : "border-edge text-live";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`rounded-[2px] border px-1.5 py-0.5 text-[9px] uppercase ${tone}`}>
        {status}
      </span>
      {verified ? <span className="text-[10px] text-violet">✓</span> : null}
    </span>
  );
}
