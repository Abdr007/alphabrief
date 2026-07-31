"use client";

import { useCallback, useMemo, useState } from "react";

import { ApprovalGate } from "@/components/ApprovalGate";
import { BriefView } from "@/components/BriefView";
import { CompletenessRail } from "@/components/CompletenessRail";
import { OrchestrationField } from "@/components/OrchestrationField";
import { TelemetryFeed } from "@/components/TelemetryFeed";
import { VerificationLattice } from "@/components/VerificationLattice";
import { PHASE_LABEL } from "@/lib/format";
import { useRunStream } from "@/lib/useRunStream";
import type { CreateRunResponse, RunMode } from "@/lib/types";

const MODES: { id: RunMode; label: string; hint: string }[] = [
  { id: "standard", label: "Standard", hint: "A normal governed run." },
  {
    id: "demo_fault",
    label: "Break a source",
    hint: "Injects a symbol no provider can answer, so you can watch it degrade instead of fail.",
  },
  {
    id: "demo_mismatch",
    label: "Corrupt a figure",
    hint: "Tampers with one number after it is written. The recompute catches it and the gate turns red.",
  },
];

export function RunConsole({ defaultWatchlist }: { defaultWatchlist: string[] }) {
  const stream = useRunStream();
  const [raw, setRaw] = useState(defaultWatchlist.join(" "));
  const [mode, setMode] = useState<RunMode>("standard");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Shared between the document and the lattice, so a figure and its proof light up together. */
  const [activeClaim, setActiveClaim] = useState<string | null>(null);

  const tickers = useMemo(
    () =>
      Array.from(
        new Set(
          raw
            .split(/[,\s]+/)
            .map((value) => value.trim().toUpperCase())
            .filter(Boolean),
        ),
      ),
    [raw],
  );

  const verificationSummary = useMemo(() => {
    const event = stream.verification;
    if (!event) return null;
    const payload = event.payload as Record<string, unknown>;
    return {
      ok: Boolean(payload.ok),
      matched: Number(payload.matched ?? 0),
      checked: Number(payload.checked_claims ?? 0),
      coverage: Number(payload.coverage ?? 0),
    };
  }, [stream.verification]);

  const launch = useCallback(async () => {
    if (tickers.length === 0) {
      setError("Enter at least one symbol.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers, mode }),
      });
      const payload = (await response.json()) as CreateRunResponse & { detail?: string };
      if (!response.ok) {
        setError(payload.detail ?? "The run could not be started.");
        return;
      }
      stream.start(payload.run_id);
    } catch {
      setError("Could not reach the console API.");
    } finally {
      setStarting(false);
    }
  }, [mode, stream, tickers]);

  const running = stream.live || starting;

  return (
    <div className="space-y-4">
      <OrchestrationField events={stream.events} runId={stream.runId} />

      <section className="grid gap-4 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="panel flex flex-col">
          <header className="border-b border-edge px-4 py-2.5">
            <span className="hdr">
              <span>Watchlist</span>
            </span>
          </header>

          <div className="flex flex-1 flex-col gap-5 px-4 py-4">
            <div>
              <textarea
                value={raw}
                onChange={(event) => setRaw(event.target.value)}
                rows={2}
                spellCheck={false}
                disabled={running}
                aria-label="Watchlist symbols"
                className="w-full resize-none rounded-[2px] border border-edge bg-void px-3 py-2.5 text-[13px] uppercase tracking-[0.08em] text-ink outline-none transition focus:border-violet-dim disabled:opacity-50"
              />
              <div className="mt-2 flex flex-wrap gap-1">
                {tickers.map((ticker) => (
                  <span
                    key={ticker}
                    className="rounded-[2px] border border-edge px-1.5 py-0.5 text-[10px] text-muted"
                  >
                    {ticker}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <span className="eyebrow">Mode</span>
              <div className="mt-2 flex flex-col gap-1">
                {MODES.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    disabled={running}
                    onClick={() => setMode(option.id)}
                    className={`btn px-3 py-2 text-left !normal-case !tracking-normal disabled:opacity-50 ${
                      mode === option.id
                        ? "border-violet-dim bg-violet-wash !text-violet"
                        : "hover:border-edge-hot hover:!text-ink"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-faint">
                {MODES.find((option) => option.id === mode)?.hint}
              </p>
            </div>

            <button
              type="button"
              onClick={launch}
              disabled={running}
              className="mt-auto w-full rounded-[2px] border border-violet bg-violet/20 py-4 text-[13px] uppercase tracking-[0.3em] text-live transition hover:bg-violet/32 disabled:border-edge disabled:bg-transparent disabled:text-faint"
            >
              {running ? PHASE_LABEL[stream.phase] : "Run"}
            </button>

            {error ? (
              <p className="rounded-[2px] border border-coral-dim bg-coral/10 px-3 py-2 text-[11px] text-coral">
                {error}
              </p>
            ) : null}
            {stream.error ? (
              <p className="rounded-[2px] border border-edge px-3 py-2 text-[11px] text-muted">
                {stream.error}
              </p>
            ) : null}

            <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 border-t border-edge pt-4 text-[11px]">
              <Readout label="Stage" value={PHASE_LABEL[stream.phase]} lit />
              <Readout label="Supervisor rounds" value={String(stream.iterations)} />
              <Readout label="Tool calls" value={String(stream.toolCallCount)} />
              <Readout label="Model calls" value={String(stream.modelCallCount)} />
            </dl>
          </div>
        </div>

        <TelemetryFeed events={stream.events} live={stream.live} />
      </section>

      {stream.runId ? (
        <CompletenessRail tickers={tickers} completeness={stream.completeness} />
      ) : null}

      {stream.claims.length > 0 || stream.quotes.length > 0 || verificationSummary ? (
        <VerificationLattice
          claims={stream.claims}
          quotes={stream.quotes}
          summary={verificationSummary}
          activeClaim={activeClaim}
          onActiveClaim={setActiveClaim}
        />
      ) : null}

      {stream.gate &&
      (stream.status === "AWAITING_APPROVAL" || stream.status === "HUMAN_REVIEW") ? (
        <ApprovalGate
          gate={stream.gate}
          onDecided={(status) => {
            stream.setStatus(status);
            void stream.refreshGate();
          }}
        />
      ) : null}

      {stream.status === "DELIVERED" || stream.status === "REJECTED" ? (
        <p
          className={`rounded-[2px] border px-4 py-2.5 text-[11.5px] ${
            stream.status === "DELIVERED"
              ? "border-violet-dim bg-violet-wash text-violet"
              : "border-coral-dim bg-coral/8 text-coral"
          }`}
        >
          {stream.status === "DELIVERED"
            ? "Approved. The brief was sent and archived with its verification report."
            : "Rejected. Nothing was sent; the run and its report stay in the archive."}
        </p>
      ) : null}

      {stream.gate?.brief ? (
        <BriefView
          brief={stream.gate.brief}
          activeClaim={activeClaim}
          onActiveClaim={setActiveClaim}
        />
      ) : null}
    </div>
  );
}

function Readout({ label, value, lit }: { label: string; value: string; lit?: boolean }) {
  return (
    <div>
      <dt className="eyebrow">{label}</dt>
      <dd className={`mt-1 ${lit ? "text-live" : "text-ink"}`}>{value}</dd>
    </div>
  );
}
