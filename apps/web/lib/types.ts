/** Shared contracts mirroring the FastAPI surface. */

export type RunMode = "standard" | "demo_fault" | "demo_mismatch";

export type EventKind =
  | "run.started"
  | "supervisor.plan"
  | "supervisor.route"
  | "agent.dispatch"
  | "agent.started"
  | "agent.completed"
  | "model.call"
  | "mcp.tool_call"
  | "state.progress"
  | "writer.started"
  | "writer.completed"
  | "verify.started"
  | "verify.claim"
  | "verify.completed"
  | "gate.awaiting"
  | "gate.decision"
  | "delivery.sent"
  | "run.completed"
  | "run.failed"
  | "warning"
  | "stream.end";

export interface TelemetryEvent {
  run_id: string;
  seq: number;
  ts: string;
  kind: EventKind;
  message: string;
  payload: Record<string, unknown>;
}

export interface ClaimCheck {
  claim_id: string;
  ticker: string;
  metric: string;
  unit: string;
  claimed: number;
  recomputed: number | null;
  delta: number | null;
  tolerance: number;
  status: "match" | "mismatch" | "unverifiable";
  detail: string;
}

export interface QuoteCheck {
  ticker: string;
  field: string;
  text: string;
  status: "match" | "mismatch";
  detail: string;
}

export interface VerificationReport {
  ok: boolean;
  checked_claims: number;
  matched: number;
  mismatched: number;
  unverifiable: number;
  coverage: number;
  claim_checks: ClaimCheck[];
  quote_checks: QuoteCheck[];
  structural_issues: string[];
  structural_warnings: string[];
}

export interface NumericClaim {
  claim_id: string;
  ticker: string;
  metric: string;
  value: number;
  unit: "usd" | "percent" | "ratio" | "score";
}

export interface SnapshotRow {
  ticker: string;
  company: string | null;
  status: "ok" | "partial" | "unavailable";
  last_close: string | null;
  change_1d: string | null;
  return_30d: string | null;
  volatility: string | null;
  max_drawdown: string | null;
  pe_ratio: string | null;
  sentiment: string | null;
  note: string | null;
}

export interface KeyMove {
  ticker: string;
  narrative: string;
  direction: "up" | "down" | "flat";
}

export interface NewsAndSentiment {
  ticker: string;
  summary: string;
  sentiment: string | null;
  top_headline: string | null;
  headline_source: string | null;
}

export interface RiskFlag {
  ticker: string;
  category: string;
  evidence: string;
  assessment: string;
}

export interface WatchItem {
  ticker: string | null;
  item: string;
}

export interface Brief {
  generated_for: string;
  watchlist: string[];
  headline: string;
  executive_summary: string;
  snapshot: SnapshotRow[];
  key_moves: KeyMove[];
  news_and_sentiment: NewsAndSentiment[];
  risk_flags: RiskFlag[];
  watch_items: WatchItem[];
  data_gaps: string[];
  claims: NumericClaim[];
  partial: boolean;
}

export interface GatePayload {
  run_id: string;
  status: string | null;
  verified: boolean;
  requires_review: boolean;
  brief: Brief;
  verification: VerificationReport;
  markdown: string;
}

export interface RunSummary {
  id: string;
  status: string;
  tickers: string[];
  mode: string;
  engine: string;
  created_at: string;
  iterations: number;
  cost_usd: number;
  latency_ms: number;
  tool_calls: number;
  partial: boolean;
  verified: boolean;
  headline: string | null;
  model_calls?: number;
  error_count?: number;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  tickers: string[];
  mode: string;
  engine: string;
  stream_url: string;
}

export type Phase =
  | "idle"
  | "dispatching"
  | "gathering"
  | "writing"
  | "verifying"
  | "awaiting_approval"
  | "delivered"
  | "rejected"
  | "failed";
