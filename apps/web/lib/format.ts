/** Presentation helpers shared by the console and the archive. */

import type { Brief, EventKind, NumericClaim, Phase } from "@/lib/types";

const CLAIM_REF = /\{\{(c\d+)\}\}/g;

export function formatClaim(claim: NumericClaim | undefined): string {
  if (!claim) return "[unverified]";
  switch (claim.unit) {
    case "usd":
      return `$${claim.value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
    case "percent":
      return `${claim.value >= 0 ? "+" : ""}${claim.value.toFixed(2)}%`;
    case "ratio":
      return claim.value.toFixed(2);
    case "score":
      return `${claim.value >= 0 ? "+" : ""}${claim.value.toFixed(2)}`;
    default:
      return String(claim.value);
  }
}

export function claimIndex(brief: Brief): Map<string, NumericClaim> {
  return new Map(brief.claims.map((claim) => [claim.claim_id, claim]));
}

/** Replace `{{cN}}` with the verified, formatted value. */
export function substituteClaims(text: string, claims: Map<string, NumericClaim>): string {
  return text.replace(CLAIM_REF, (_match, id: string) => formatClaim(claims.get(id)));
}

export function cell(id: string | null, claims: Map<string, NumericClaim>): string {
  if (!id) return "—";
  return formatClaim(claims.get(id));
}

export function signOf(id: string | null, claims: Map<string, NumericClaim>): number {
  if (!id) return 0;
  const claim = claims.get(id);
  return claim ? Math.sign(claim.value) : 0;
}

/** Terminal channel codes — fixed width so the feed stays in columns. */
const KIND_STYLES: Record<string, { label: string; tone: string }> = {
  "run.started": { label: "boot", tone: "text-amber" },
  "supervisor.plan": { label: "supv", tone: "text-signal" },
  "supervisor.route": { label: "rout", tone: "text-signal" },
  "agent.dispatch": { label: "disp", tone: "text-signal" },
  "agent.started": { label: "agnt", tone: "text-amber" },
  "agent.completed": { label: "agnt", tone: "text-phosphor" },
  "model.call": { label: "mdl", tone: "text-signal" },
  "mcp.tool_call": { label: "mcp", tone: "text-amber" },
  "state.progress": { label: "stat", tone: "text-faint" },
  "writer.started": { label: "wrtr", tone: "text-signal" },
  "writer.completed": { label: "wrtr", tone: "text-phosphor" },
  "verify.started": { label: "vrfy", tone: "text-amber" },
  "verify.claim": { label: "clm", tone: "text-phosphor" },
  "verify.completed": { label: "vrfy", tone: "text-phosphor" },
  "gate.awaiting": { label: "gate", tone: "text-amber" },
  "gate.decision": { label: "gate", tone: "text-signal" },
  "delivery.sent": { label: "dlvr", tone: "text-phosphor" },
  "run.completed": { label: "done", tone: "text-phosphor" },
  "run.failed": { label: "fail", tone: "text-alert" },
  warning: { label: "warn", tone: "text-alert" },
};

export function kindStyle(kind: EventKind): { label: string; tone: string } {
  return KIND_STYLES[kind] ?? { label: kind.toUpperCase().slice(0, 9), tone: "text-muted" };
}

export function phaseFromEvents(kinds: EventKind[], status: string | null): Phase {
  if (status === "DELIVERED") return "delivered";
  if (status === "REJECTED") return "rejected";
  if (status === "FAILED" || status === "BUDGET_ABORT" || status === "ITERATION_ABORT") {
    return "failed";
  }
  if (kinds.includes("gate.awaiting")) return "awaiting_approval";
  if (kinds.includes("verify.started")) return "verifying";
  if (kinds.includes("writer.started")) return "writing";
  if (kinds.includes("agent.started")) return "gathering";
  if (kinds.includes("run.started")) return "dispatching";
  return "idle";
}

export const PHASE_LABEL: Record<Phase, string> = {
  idle: "STANDING BY",
  dispatching: "PLANNING",
  gathering: "GATHERING",
  writing: "ASSEMBLING",
  verifying: "RECOMPUTING",
  awaiting_approval: "AWAITING SIGN-OFF",
  delivered: "DELIVERED",
  rejected: "REJECTED",
  failed: "HALTED",
};

/** Step order for the pipeline rail in the console header. */
export const PHASE_SEQUENCE: Phase[] = [
  "dispatching",
  "gathering",
  "writing",
  "verifying",
  "awaiting_approval",
];

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function clockOf(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toISOString().slice(11, 23);
}
