"use client";

import { useEffect, useRef } from "react";

import { clockOf, kindStyle } from "@/lib/format";
import type { TelemetryEvent } from "@/lib/types";

/**
 * Mission telemetry: timestamp, channel, message — one fixed-width row per
 * event, scrolling. Each MCP tool call shows its arguments and its millisecond
 * timing, because that is the thing worth watching.
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
    <section className="panel ticked relative flex h-full min-h-[460px] flex-col">
      <header className="flex items-center justify-between rule-b px-3 py-2">
        <span className="hdr flex-1">
          <span>Agent activity</span>
        </span>
        <span className="ml-3 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]">
          {live ? (
            <span className="text-amber">
              <span className="mr-1.5 inline-block h-1.5 w-1.5 bg-amber align-middle blink" />
              streaming
            </span>
          ) : (
            <span className="text-faint">idle</span>
          )}
          <span className="text-faint">{String(events.length).padStart(3, "0")} evt</span>
        </span>
      </header>

      <div
        ref={scroller}
        onScroll={onScroll}
        className={`relative flex-1 overflow-y-auto px-2 py-1.5 text-[11.5px] leading-[1.55] ${
          live ? "scanning" : ""
        }`}
        role="log"
        aria-live="polite"
        aria-label="Agent activity feed"
      >
        {events.length === 0 ? (
          <div className="px-2 py-16 text-center">
            <p className="text-[11px] uppercase tracking-[0.2em] text-faint">
              awaiting dispatch
            </p>
            <p className="mt-2 text-[11px] text-rule-hot">
              <span className="inline-block h-3 w-[7px] bg-amber-dim align-middle blink" />
            </p>
          </div>
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
    <div className="tick-in grid grid-cols-[92px_58px_1fr] items-start gap-x-2 px-1.5 py-[3px] hover:bg-raised">
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
        <span className={ok ? "text-amber/90" : "text-alert"}>
          {String(event.payload.tool ?? "tool")}
        </span>
        <span>(</span>
        {args
          ? Object.entries(args).map(([key, value], index) => (
              <span key={key}>
                {index > 0 ? <span>, </span> : null}
                <span className="text-muted">{key}</span>
                <span className="text-rule-hot">=</span>
                <span className="text-ink/70">{String(value)}</span>
              </span>
            ))
          : null}
        <span>)</span>
        <span className="mx-2 text-rule-hot">{"─".repeat(3)}</span>
        <span className={ok ? "text-phosphor" : "text-alert"}>
          {Math.round(duration)}ms {latencyBlocks(duration)}
        </span>
        {summary ? <span className="ml-2 text-muted">▸ {String(summary)}</span> : null}
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
        <span className="text-signal">{String(event.payload.role ?? "")}</span>
        <span className="mx-1.5 text-rule-hot">▸</span>
        {String(event.payload.model ?? "")}
      </span>
    );
  }

  return null;
}

/** A crude latency bar, in blocks — reads at a glance in a dense feed. */
function latencyBlocks(ms: number): string {
  const filled = Math.min(5, Math.max(1, Math.ceil(ms / 250)));
  return "▓".repeat(filled) + "░".repeat(5 - filled);
}
