"use client";

import { useMemo } from "react";

import type { TelemetryEvent } from "@/lib/types";

/**
 * The graph, drawn as the graph.
 *
 * A row of pills implies a pipeline, and this is not one: the supervisor fans
 * two workers out *in parallel* and loops back to re-plan until the watchlist
 * is complete. Drawing the real topology — with the fan-out and the return
 * path visible — is the difference between saying "multi-agent" and showing it.
 */

type NodeId = "supervisor" | "data" | "news" | "writer" | "verify" | "gate";
type NodeState = "idle" | "live" | "done";

/** Tools are whitelisted per agent, so a call attributes to exactly one node. */
const TOOL_OWNER: Record<string, "data" | "news"> = {
  get_price_history: "data",
  get_fundamentals: "data",
  compute_metrics: "data",
  fetch_rss_news: "news",
};

interface FieldState {
  nodes: Record<NodeId, NodeState>;
  toolCalls: { data: number; news: number };
  iteration: number;
  claimsChecked: number;
}

const IDLE: Record<NodeId, NodeState> = {
  supervisor: "idle",
  data: "idle",
  news: "idle",
  writer: "idle",
  verify: "idle",
  gate: "idle",
};

function agentOf(event: TelemetryEvent): "data" | "news" | null {
  const name = event.payload.agent;
  if (name === "data_agent") return "data";
  if (name === "news_agent") return "news";
  return null;
}

/** A node that was executing when the graph moved on has finished, not stalled. */
function settle(state: NodeState): NodeState {
  return state === "live" ? "done" : state;
}

function reduceField(events: TelemetryEvent[]): FieldState {
  const nodes: Record<NodeId, NodeState> = { ...IDLE };
  const toolCalls = { data: 0, news: 0 };
  let iteration = 0;
  let claimsChecked = 0;

  for (const event of events) {
    switch (event.kind) {
      case "run.started":
      case "supervisor.plan":
      case "supervisor.route":
        nodes.supervisor = "live";
        if (typeof event.payload.iteration === "number") {
          iteration = Math.max(iteration, event.payload.iteration);
        }
        break;

      case "agent.dispatch":
      case "agent.started": {
        const agent = agentOf(event);
        if (agent) nodes[agent] = "live";
        nodes.supervisor = settle(nodes.supervisor);
        break;
      }

      case "agent.completed": {
        const agent = agentOf(event);
        if (agent) nodes[agent] = "done";
        break;
      }

      case "mcp.tool_call": {
        const owner = TOOL_OWNER[String(event.payload.tool ?? "")];
        if (owner) toolCalls[owner] += 1;
        break;
      }

      case "writer.started":
        nodes.writer = "live";
        nodes.supervisor = settle(nodes.supervisor);
        nodes.data = settle(nodes.data);
        nodes.news = settle(nodes.news);
        break;

      case "writer.completed":
        nodes.writer = "done";
        break;

      case "verify.started":
        nodes.verify = "live";
        nodes.writer = settle(nodes.writer);
        break;

      case "verify.claim":
        claimsChecked += 1;
        break;

      case "verify.completed":
        nodes.verify = "done";
        break;

      case "gate.awaiting":
        nodes.gate = "live";
        nodes.verify = settle(nodes.verify);
        break;

      case "gate.decision":
        nodes.gate = "done";
        break;

      case "run.failed":
        // Whatever was mid-flight stopped there; nothing further ignites.
        for (const id of Object.keys(nodes) as NodeId[]) {
          if (nodes[id] === "live") nodes[id] = "idle";
        }
        break;

      default:
        break;
    }
  }

  return { nodes, toolCalls, iteration, claimsChecked };
}

export function OrchestrationField({
  events,
  runId,
}: {
  events: TelemetryEvent[];
  runId: string | null;
}) {
  const field = useMemo(() => reduceField(events), [events]);
  const { nodes } = field;

  return (
    <section className="panel px-4 py-4">
      <div className="mb-4 flex items-center gap-3">
        <span className="hdr flex-1">
          <span>Orchestration</span>
        </span>
        <span className="text-[10px] uppercase tracking-[0.16em] text-faint">
          {runId ? (
            <>
              run <span className="text-muted">{runId.replace("run_", "").slice(0, 10)}</span>
            </>
          ) : (
            "no active run"
          )}
        </span>
      </div>

      {/* The topology. Horizontal on wide screens so the parallel fan-out is
          legible at a glance; stacked below lg, where a 5-station row cannot
          breathe. */}
      <div className="flex flex-col items-stretch gap-3 lg:flex-row lg:items-center lg:gap-0">
        <Node
          id="supervisor"
          name="Supervisor"
          detail={field.iteration > 0 ? `iteration ${field.iteration}` : "plans the round"}
          state={nodes.supervisor}
          model="haiku"
        />

        <Link active={nodes.data === "live" || nodes.news === "live"} label="fan out" />

        {/* The parallel pair — the whole reason this is a graph and not a chain.
            Bracketed so the fan-out reads as one simultaneous step rather than
            two consecutive boxes that happen to be stacked. */}
        <div className="relative rounded-[3px] border border-dashed border-edge-hot px-2 pb-5 pt-4 lg:min-w-[186px]">
          <span className="absolute -top-2 left-2 bg-void px-1.5 text-[9px] uppercase tracking-[0.18em] text-faint">
            in parallel
          </span>
          <div className="flex flex-col gap-2">
            <Node
              id="data"
              name="Data agent"
              detail={`${field.toolCalls.data} tool call${field.toolCalls.data === 1 ? "" : "s"}`}
              state={nodes.data}
              model="sonnet"
              compact
            />
            <Node
              id="news"
              name="News agent"
              detail={`${field.toolCalls.news} tool call${field.toolCalls.news === 1 ? "" : "s"}`}
              state={nodes.news}
              model="sonnet"
              compact
            />
          </div>
          <span className="absolute -bottom-2 left-1/2 -translate-x-1/2 whitespace-nowrap bg-void px-1.5 text-[9px] uppercase tracking-[0.18em] text-faint">
            ↺ re-plans until complete
          </span>
        </div>

        <Link active={nodes.writer === "live"} label="join" />

        <Node
          id="writer"
          name="Writer"
          detail="cites, never computes"
          state={nodes.writer}
          model="sonnet"
        />

        <Link active={nodes.verify === "live"} label="" />

        <Node
          id="verify"
          name="Verifier"
          detail={
            field.claimsChecked > 0 ? `${field.claimsChecked} recomputed` : "recomputes every figure"
          }
          state={nodes.verify}
          model="code"
        />

        <Link active={nodes.gate === "live"} label="" />

        <Node
          id="gate"
          name="Human gate"
          detail="nothing ships unsigned"
          state={nodes.gate}
          model="human"
        />
      </div>
    </section>
  );
}

const MODEL_LABEL: Record<string, string> = {
  haiku: "haiku 4.5",
  sonnet: "sonnet 4.6",
  code: "deterministic code",
  human: "a person",
};

function Node({
  name,
  detail,
  state,
  model,
  compact = false,
}: {
  id: NodeId;
  name: string;
  detail: string;
  state: NodeState;
  model: keyof typeof MODEL_LABEL;
  compact?: boolean;
}) {
  const tone =
    state === "live"
      ? "ignite text-live"
      : state === "done"
        ? "settled text-violet"
        : "border-edge-hot text-muted";

  return (
    <div
      className={`flex-1 rounded-[3px] border px-3 transition-colors lg:min-w-[132px] ${
        compact ? "py-2" : "py-2.5"
      } ${tone}`}
      // Screen readers get the state in words; colour and glow are not enough.
      aria-label={`${name}: ${state === "live" ? "running" : state === "done" ? "finished" : "not started"}`}
    >
      <div className="flex items-center gap-1.5">
        {state === "live" ? (
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-live blink" aria-hidden />
        ) : state === "done" ? (
          <span className="text-[9px] text-violet" aria-hidden>
            ✓
          </span>
        ) : (
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-edge-hot" aria-hidden />
        )}
        <span
          className={`display text-[13.5px] font-semibold leading-none ${
            state === "idle" ? "text-ink" : ""
          }`}
        >
          {name}
        </span>
      </div>
      <p className="mt-1.5 text-[10.5px] leading-tight text-muted">{detail}</p>
      <p className="mt-1 text-[9px] uppercase tracking-[0.14em] text-faint">
        {MODEL_LABEL[model]}
      </p>
    </div>
  );
}

/** A directed edge. It carries a charge only while the node it feeds is live. */
function Link({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="flex shrink-0 items-center justify-center px-2 lg:w-[54px] lg:flex-col lg:gap-1">
      <div
        className={`h-[2px] w-full min-w-[24px] rounded-full ${active ? "charged" : "bg-edge-hot"}`}
        aria-hidden
      />
      {label ? (
        <span className="ml-2 whitespace-nowrap text-[9px] uppercase tracking-[0.14em] text-faint lg:ml-0">
          {label}
        </span>
      ) : null}
    </div>
  );
}
