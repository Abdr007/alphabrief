"use client";

import { useCallback, useMemo, useState } from "react";

import { ApprovalGate } from "@/components/ApprovalGate";
import { BriefView } from "@/components/BriefView";
import { CompletenessRail } from "@/components/CompletenessRail";
import { TelemetryFeed } from "@/components/TelemetryFeed";
import { VerificationPanel } from "@/components/VerificationPanel";
import { PHASE_LABEL, PHASE_SEQUENCE } from "@/lib/format";
import { useRunStream } from "@/lib/useRunStream";
import type { CreateRunResponse, Phase, RunMode } from "@/lib/types";

const MODES: { id: RunMode; code: string; hint: string }[] = [
  { id: "standard", code: "STD", hint: "normal governed run" },
  { id: "demo_fault", code: "FLT", hint: "injects a dead ticker — graceful degradation" },
  { id: "demo_mismatch", code: "MSM", hint: "corrupts a figure — the verifier goes red" },
];

export function RunConsole({ defaultWatchlist }: { defaultWatchlist: string[] }) {
  const stream = useRunStream();
  const [raw, setRaw] = useState(defaultWatchlist.join(" "));
  const [mode, setMode] = useState<RunMode>("standard");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setError("At least one symbol is required.");
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
        setError(payload.detail ?? "Could not start the run.");
        return;
      }
      stream.start(payload.run_id);
    } catch {
      setError("Network error while starting the run.");
    } finally {
      setStarting(false);
    }
  }, [mode, stream, tickers]);

  const running = stream.live || starting;

  return (
    <div className="space-y-4">
      {/* ── pipeline rail ─────────────────────────────────────────────── */}
      <PipelineRail phase={stream.phase} runId={stream.runId} />

      {/* ── launch bay + telemetry ────────────────────────────────────── */}
      <section className="grid gap-4 lg:grid-cols-[minmax(0,330px)_minmax(0,1fr)]">
        <div className="panel ticked flex flex-col">
          <header className="rule-b px-3 py-2">
            <span className="hdr">
              <span>Watchlist</span>
            </span>
          </header>

          <div className="flex flex-1 flex-col gap-4 px-3 py-3">
            <div>
              <textarea
                value={raw}
                onChange={(event) => setRaw(event.target.value)}
                rows={2}
                spellCheck={false}
                disabled={running}
                aria-label="Watchlist symbols"
                className="w-full resize-none border border-rule bg-black px-2.5 py-2 text-[13px] uppercase tracking-[0.08em] text-amber outline-none transition focus:border-amber-dim disabled:opacity-50"
              />
              <div className="mt-1.5 flex flex-wrap gap-1">
                {tickers.map((ticker) => (
                  <span
                    key={ticker}
                    className="border border-rule px-1.5 py-0.5 text-[10px] text-muted"
                  >
                    ▸{ticker}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <span className="label">mode</span>
              <div className="mt-1.5 grid grid-cols-3 gap-1">
                {MODES.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    title={option.hint}
                    disabled={running}
                    onClick={() => setMode(option.id)}
                    className={`border py-1.5 text-[11px] tracking-[0.14em] transition disabled:opacity-50 ${
                      mode === option.id
                        ? "border-amber-dim bg-amber/12 text-amber"
                        : "border-rule text-faint hover:border-rule-hot hover:text-ink"
                    }`}
                  >
                    {option.code}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-[10px] leading-relaxed text-faint">
                {MODES.find((option) => option.id === mode)?.hint}
              </p>
            </div>

            {/* the key */}
            <button
              type="button"
              onClick={launch}
              disabled={running}
              className={`relative mt-auto h-[72px] w-full border text-[17px] tracking-[0.42em] transition ${
                running
                  ? "border-rule-hot bg-raised text-faint"
                  : "key-live border-amber-dim bg-amber/8 text-amber hover:bg-amber/16"
              }`}
            >
              {running ? (
                <>
                  <span className="inline-block h-3 w-[8px] bg-amber-dim align-middle blink" />
                  <span className="ml-3">BUSY</span>
                </>
              ) : (
                <span className="glow-amber">RUN</span>
              )}
            </button>

            {error ? (
              <p className="border border-alert-dim bg-alert/10 px-2.5 py-1.5 text-[11px] text-alert">
                {error}
              </p>
            ) : null}
            {stream.error ? (
              <p className="border border-rule px-2.5 py-1.5 text-[11px] text-muted">
                {stream.error}
              </p>
            ) : null}

            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-rule pt-3 text-[11px]">
              <Readout label="phase" value={PHASE_LABEL[stream.phase]} tone="amber" />
              <Readout label="iter" value={String(stream.iterations)} />
              <Readout label="mcp calls" value={String(stream.toolCallCount)} />
              <Readout label="model calls" value={String(stream.modelCallCount)} />
            </dl>
          </div>
        </div>

        <TelemetryFeed events={stream.events} live={stream.live} />
      </section>

      {stream.runId ? (
        <CompletenessRail tickers={tickers} completeness={stream.completeness} />
      ) : null}

      {stream.claims.length > 0 || verificationSummary ? (
        <VerificationPanel claims={stream.claims} summary={verificationSummary} />
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
          className={`border px-3 py-2 text-[11.5px] ${
            stream.status === "DELIVERED"
              ? "border-phosphor-dim bg-phosphor/8 text-phosphor"
              : "border-alert-dim bg-alert/8 text-alert"
          }`}
        >
          {stream.status === "DELIVERED"
            ? "◆ APPROVED — brief emailed and archived with its verification report attached."
            : "✕ REJECTED — nothing delivered. The run and its report remain in the archive."}
        </p>
      ) : null}

      {stream.gate?.brief ? <BriefView brief={stream.gate.brief} /> : null}
    </div>
  );
}

/** Horizontal pipeline rail — shows exactly where the graph is. */
function PipelineRail({ phase, runId }: { phase: Phase; runId: string | null }) {
  const activeIndex = PHASE_SEQUENCE.indexOf(phase);
  const terminal = phase === "delivered" || phase === "rejected" || phase === "failed";

  return (
    <section className="panel flex flex-wrap items-center gap-x-1 gap-y-2 px-3 py-2">
      {PHASE_SEQUENCE.map((step, index) => {
        const done = terminal || (activeIndex >= 0 && index < activeIndex);
        const active = index === activeIndex && !terminal;
        return (
          <span key={step} className="flex items-center">
            <span
              className={`border px-2 py-1 text-[10px] uppercase tracking-[0.14em] ${
                active
                  ? "border-amber-dim bg-amber/12 text-amber"
                  : done
                    ? "border-phosphor-dim text-phosphor"
                    : "border-rule text-faint"
              }`}
            >
              {done && !active ? "✓ " : null}
              {PHASE_LABEL[step]}
            </span>
            {index < PHASE_SEQUENCE.length - 1 ? (
              <span className={`px-1 ${done ? "text-phosphor-dim" : "text-rule-hot"}`}>──</span>
            ) : null}
          </span>
        );
      })}
      <span className="ml-auto text-[10px] uppercase tracking-[0.14em] text-faint">
        {runId ? (
          <>
            run<span className="mx-1 text-rule-hot">:</span>
            <span className="text-muted">{runId.replace("run_", "").slice(0, 10)}</span>
          </>
        ) : (
          "no active run"
        )}
      </span>
    </section>
  );
}

function Readout({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "amber";
}) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`mt-0.5 ${tone === "amber" ? "text-amber" : "text-ink"}`}>{value}</dd>
    </div>
  );
}
