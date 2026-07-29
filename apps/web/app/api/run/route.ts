import { NextResponse } from "next/server";

import { ApiError, DEFAULT_WATCHLIST, callApi } from "@/lib/server";
import type { CreateRunResponse, RunMode } from "@/lib/types";

export const dynamic = "force-dynamic";

const MODES: readonly RunMode[] = ["standard", "demo_fault", "demo_mismatch"];
const MAX_TICKERS = 12;
const TICKER_PATTERN = /^[A-Z0-9.^-]{1,12}$/;

/** Trigger a research run. Input is re-validated here as well as in the API. */
export async function POST(request: Request): Promise<NextResponse> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Body must be JSON." }, { status: 400 });
  }

  const body = (payload ?? {}) as { tickers?: unknown; mode?: unknown };

  const mode: RunMode = MODES.includes(body.mode as RunMode)
    ? (body.mode as RunMode)
    : "standard";

  let tickers = DEFAULT_WATCHLIST;
  if (Array.isArray(body.tickers)) {
    const cleaned = Array.from(
      new Set(
        body.tickers
          .filter((value): value is string => typeof value === "string")
          .map((value) => value.trim().toUpperCase())
          .filter(Boolean),
      ),
    );
    if (cleaned.length === 0) {
      return NextResponse.json({ detail: "At least one ticker is required." }, { status: 400 });
    }
    if (cleaned.length > MAX_TICKERS) {
      return NextResponse.json(
        { detail: `A watchlist may contain at most ${MAX_TICKERS} tickers.` },
        { status: 400 },
      );
    }
    const invalid = cleaned.find((ticker) => !TICKER_PATTERN.test(ticker));
    if (invalid) {
      return NextResponse.json(
        { detail: `"${invalid.slice(0, 16)}" is not a valid ticker symbol.` },
        { status: 400 },
      );
    }
    tickers = cleaned;
  }

  try {
    const created = await callApi<CreateRunResponse>("/v1/runs", {
      method: "POST",
      body: { tickers, mode },
    });
    return NextResponse.json(created, { status: 202 });
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "Could not start the run.";
    return NextResponse.json({ detail }, { status });
  }
}
