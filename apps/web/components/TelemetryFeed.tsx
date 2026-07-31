"use client";

import { useEffect, useRef } from "react";

import { clockOf, kindStyle } from "@/lib/format";
import type { TelemetryEvent } from "@/lib/types";

/**
 * The raw trace: timestamp, channel, message — one fixed-width row per event.
 *
 * Every MCP tool call shows its arguments and its millisecond timing, because
 * that is the part worth watching: it is the evidence that the figures came
 * from a tool rather than from a model's memory.
 */
export function TelemetryFeed({ events, live }: { events: TelemetryEvent[]; live: boolean }) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const node = scroller.current;
    if (!node || !pinned.current) return;
    node.scrollTop = node.scrollHeight;
  }, [events]);

  const onScroll = () => {
    const node = scroller.current;
    if (!node) return;
    pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
  };

  return (
    <section className="panel flex h-full min-h-[420px] flex-col">
      <header className="flex items-center gap-3 border-b border-edge px-4 py-2.5">
        <span className="hdr flex-1">
          <span>Trace</span>
        </span>
        <span className="flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]">
          {live ? (
            <span className="text-live">
              <span
                className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-live align-middle blink"
                aria-hidden
              />
              streaming
            </span>
          ) : (
            <span className="text-faint">idle</span>
          )}
          <span className="text-faint">{events.length} events</span>
        </span>
      </header>

      <div
        ref={scroller}
        onScroll={onScroll}
        className="thin-scroll flex-1 overflow-y-auto px-2 py-2 text-[11.5px] leading-[1.55]"
        role="log"
        aria-live="polite"
        aria-label="Agent activity trace"
      >
        {events.length === 0 ? (
          <p className="px-2 py-20 text-center text-[11px] uppercase tracking-[0.2em] text-faint">
            no run in flight
          </p>
        ) : null}

        {events.map((event) => (
          <Row key={`${event.run_id}-${event.seq}`} event={event} />
        ))}
      </div>
    </section>
  );
}

function Row({ event }: { event: TelemetryEvent }) {
  const style = kindStyle(event.kind);
  return (
    <div className="tick-in grid grid-cols-[86px_46px_1fr] items-start gap-x-2 rounded-[2px] px-1.5 py-[3px] hover:bg-raised">
      <span className="text-faint">{clockOf(event.ts)}</span>
      <span className={`uppercase ${style.tone}`}>{style.label}</span>
      <span className="min-w-0">
        <span className="block break-words text-ink/85">{event.message}</span>
        <Detail event={event} />
      </span>
    </div>
  );
}

function Detail({ event }: { event: TelemetryEvent }) {
  if (event.kind === "mcp.tool_call") {
    const args = event.payload.arguments as Record<string, unknown> | undefined;
    const ok = event.payload.ok !== false;
    const duration = Number(event.payload.duration_ms ?? 0);
    const summary = event.payload.summary;
    return (
      <span className="mt-[1px] block text-[11px] text-faint">
        <span className={ok ? "text-live" : "text-coral"}>
          {String(event.payload.tool ?? "tool")}
        </span>
        <span>(</span>
        {args
          ? Object.entries(args).map(([key, value], index) => (
              <span key={key}>
                {index > 0 ? <span>, </span> : null}
                <span className="text-muted">{key}</span>
                <span className="text-edge-hot">=</span>
                <span className="text-ink/70">{String(value)}</span>
              </span>
            ))
          : null}
        <span>)</span>
        <span className={`ml-2 ${ok ? "text-violet" : "text-coral"}`}>
          {Math.round(duration)}ms
        </span>
        {summary ? <span className="ml-2 text-muted">{String(summary)}</span> : null}
      </span>
    );
  }

  if (event.kind === "supervisor.plan") {
    const market = event.payload.market_tickers as string[] | undefined;
    const news = event.payload.news_tickers as string[] | undefined;
    if (!market?.length && !news?.length) return null;
    return (
      <span className="mt-[1px] block text-[11px] text-faint">
        {market?.length ? (
          <>
            <span className="text-muted">market</span>=[{market.join(" ")}]{" "}
          </>
        ) : null}
        {news?.length ? (
          <>
            <span className="text-muted">news</span>=[{news.join(" ")}]
          </>
        ) : null}
      </span>
    );
  }

  if (event.kind === "model.call") {
    return (
      <span className="mt-[1px] block text-[11px] text-faint">
        <span className="text-muted">{String(event.payload.role ?? "")}</span>
        <span className="mx-1.5 text-edge-hot">/</span>
        {String(event.payload.model ?? "")}
      </span>
    );
  }

  return null;
}
