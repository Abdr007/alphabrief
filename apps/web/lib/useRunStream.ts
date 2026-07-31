"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { phaseFromEvents } from "@/lib/format";
import type {
  ClaimCheck,
  EventKind,
  GatePayload,
  Phase,
  QuoteCheck,
  TelemetryEvent,
} from "@/lib/types";

/** Every named SSE event the API can emit; EventSource needs explicit listeners. */
const EVENT_KINDS: EventKind[] = [
  "run.started",
  "supervisor.plan",
  "supervisor.route",
  "agent.dispatch",
  "agent.started",
  "agent.completed",
  "model.call",
  "mcp.tool_call",
  "state.progress",
  "writer.started",
  "writer.completed",
  "verify.started",
  "verify.claim",
  "verify.completed",
  "gate.awaiting",
  "gate.decision",
  "delivery.sent",
  "run.completed",
  "run.failed",
  "warning",
  "stream.end",
];

const TERMINAL: ReadonlySet<EventKind> = new Set(["run.completed", "run.failed", "stream.end"]);

export interface RunStreamState {
  runId: string | null;
  events: TelemetryEvent[];
  phase: Phase;
  live: boolean;
  completeness: Record<string, number>;
  claims: ClaimCheck[];
  quotes: QuoteCheck[];
  verification: TelemetryEvent | null;
  gate: GatePayload | null;
  status: string | null;
  error: string | null;
  toolCallCount: number;
  modelCallCount: number;
  iterations: number;
}

export interface RunStreamApi extends RunStreamState {
  start: (runId: string) => void;
  reset: () => void;
  setStatus: (status: string) => void;
  refreshGate: () => Promise<void>;
}

export function useRunStream(): RunStreamApi {
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [live, setLive] = useState(false);
  const [gate, setGate] = useState<GatePayload | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const seenRef = useRef<Set<number>>(new Set());

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setLive(false);
  }, []);

  const fetchGate = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/gate/${id}`, { cache: "no-store" });
      if (!response.ok) return;
      const payload = (await response.json()) as GatePayload;
      setGate(payload);
    } catch {
      // The gate simply isn't ready yet; the stream will tell us when it is.
    }
  }, []);

  const start = useCallback(
    (id: string) => {
      close();
      seenRef.current = new Set();
      setRunId(id);
      setEvents([]);
      setGate(null);
      setStatus("RUNNING");
      setError(null);
      setLive(true);

      const source = new EventSource(`/api/stream/${id}`);
      sourceRef.current = source;

      const handle = (raw: MessageEvent<string>) => {
        let event: TelemetryEvent;
        try {
          event = JSON.parse(raw.data) as TelemetryEvent;
        } catch {
          return;
        }
        if (typeof event.seq === "number") {
          if (seenRef.current.has(event.seq)) return;
          seenRef.current.add(event.seq);
        }
        setEvents((current) => [...current, event]);

        if (event.kind === "gate.awaiting") {
          setStatus(String(event.payload.status ?? "AWAITING_APPROVAL"));
          void fetchGate(id);
        }
        if (event.kind === "run.completed" || event.kind === "run.failed") {
          const next = event.payload.status;
          setStatus(typeof next === "string" ? next : event.kind === "run.failed" ? "FAILED" : null);
          void fetchGate(id);
        }
        if (TERMINAL.has(event.kind)) {
          close();
        }
      };

      for (const kind of EVENT_KINDS) {
        source.addEventListener(kind, handle as EventListener);
      }
      source.onmessage = handle;
      source.onerror = () => {
        // The API closes the stream when the run reaches a terminal state; that
        // surfaces here as an error, so only report it if we never got going.
        setLive(false);
        setEvents((current) => {
          if (current.length === 0) {
            setError("Lost connection to the telemetry stream.");
          }
          return current;
        });
        source.close();
      };
    },
    [close, fetchGate],
  );

  const reset = useCallback(() => {
    close();
    setRunId(null);
    setEvents([]);
    setGate(null);
    setStatus(null);
    setError(null);
  }, [close]);

  const refreshGate = useCallback(async () => {
    if (runId) await fetchGate(runId);
  }, [fetchGate, runId]);

  useEffect(() => close, [close]);

  const derived = useMemo(() => {
    const kinds = events.map((event) => event.kind);
    const completeness: Record<string, number> = {};
    let iterations = 0;

    for (const event of events) {
      if (event.kind === "state.progress") {
        const value = event.payload.completeness;
        if (value && typeof value === "object") {
          Object.assign(completeness, value as Record<string, number>);
        }
        const iteration = event.payload.iteration;
        if (typeof iteration === "number") iterations = Math.max(iterations, iteration);
      }
    }

    // `verify.claim` carries two different shapes: a numeric ClaimCheck, and a
    // QuoteCheck for a headline the writer quoted — which has no claim_id,
    // claimed value or tolerance. They are separated here rather than at the
    // render site so no consumer can mistake one for the other.
    const verifications = events.filter((event) => event.kind === "verify.claim");
    // A mismatch costs the writer one regeneration, and verification then runs
    // again over the same claim ids. Keeping the last report per id shows the
    // final state; keeping them all would double every row and contradict the
    // summary count.
    const latestClaims = new Map<string, ClaimCheck>();
    for (const event of verifications) {
      const id = event.payload.claim_id;
      if (typeof id === "string") {
        latestClaims.set(id, event.payload as unknown as ClaimCheck);
      }
    }
    const claims = [...latestClaims.values()];
    const latestQuotes = new Map<string, QuoteCheck>();
    for (const event of verifications) {
      if (typeof event.payload.claim_id === "string") continue;
      const quote = event.payload as unknown as QuoteCheck;
      latestQuotes.set(`${quote.ticker}:${quote.field}`, quote);
    }
    const quotes = [...latestQuotes.values()];

    const verification =
      events.filter((event) => event.kind === "verify.completed").slice(-1)[0] ?? null;

    return {
      phase: phaseFromEvents(kinds, status),
      completeness,
      claims,
      quotes,
      verification,
      iterations,
      toolCallCount: kinds.filter((kind) => kind === "mcp.tool_call").length,
      modelCallCount: kinds.filter((kind) => kind === "model.call").length,
    };
  }, [events, status]);

  return {
    runId,
    events,
    live,
    gate,
    status,
    error,
    start,
    reset,
    setStatus,
    refreshGate,
    ...derived,
  };
}
