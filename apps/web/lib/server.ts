/**
 * Server-only access to the AlphaBrief API.
 *
 * The browser never calls the API directly. Every request is proxied through a
 * Next route handler so that the approval token and the API origin stay on the
 * server — there is deliberately no `NEXT_PUBLIC_*` variable for either.
 */

import "server-only";

export const API_BASE = (process.env.ALPHABRIEF_API_URL ?? "http://127.0.0.1:7860").replace(
  /\/+$/,
  "",
);

const APPROVAL_TOKEN = process.env.ALPHABRIEF_APPROVAL_TOKEN ?? "";
const CRON_SECRET = process.env.CRON_SECRET ?? "";

export const DEFAULT_WATCHLIST = (
  process.env.ALPHABRIEF_DEFAULT_WATCHLIST ?? "AAPL,MSFT,NVDA,TSLA,AMZN"
)
  .split(",")
  .map((ticker) => ticker.trim().toUpperCase())
  .filter(Boolean);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ApiOptions {
  method?: "GET" | "POST";
  body?: unknown;
  /** Attach the approval bearer token (never exposed to the browser). */
  authenticated?: boolean;
  /** Abort after this many milliseconds. */
  timeoutMs?: number;
  cache?: RequestCache;
}

export async function callApi<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { method = "GET", body, authenticated = false, timeoutMs = 20_000, cache = "no-store" } =
    options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authenticated) {
    if (!APPROVAL_TOKEN) {
      throw new ApiError(503, "ALPHABRIEF_APPROVAL_TOKEN is not configured on the server.");
    }
    headers.Authorization = `Bearer ${APPROVAL_TOKEN}`;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      cache,
      signal: controller.signal,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });

    const text = await response.text();
    const parsed: unknown = text ? safeJson(text) : null;

    if (!response.ok) {
      throw new ApiError(response.status, extractDetail(parsed) ?? `API error ${response.status}`);
    }
    return parsed as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiError(504, "The AlphaBrief API did not respond in time.");
    }
    throw new ApiError(
      502,
      `Cannot reach the AlphaBrief API at ${API_BASE}. Is it running?`,
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Open the upstream SSE stream for proxying. */
export async function openStream(runId: string): Promise<Response> {
  return fetch(`${API_BASE}/v1/runs/${encodeURIComponent(runId)}/stream`, {
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
  });
}

/** Vercel Cron sends `Authorization: Bearer $CRON_SECRET`. Verify it when set. */
export function isAuthorisedCron(authorization: string | null): boolean {
  if (!CRON_SECRET) return true;
  if (!authorization) return false;
  const expected = `Bearer ${CRON_SECRET}`;
  if (authorization.length !== expected.length) return false;
  let mismatch = 0;
  for (let index = 0; index < expected.length; index += 1) {
    mismatch |= authorization.charCodeAt(index) ^ expected.charCodeAt(index);
  }
  return mismatch === 0;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 400) };
  }
}

function extractDetail(payload: unknown): string | null {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}
