import { NextResponse } from "next/server";

import { ApiError, DEFAULT_WATCHLIST, callApi, isAuthorisedCron } from "@/lib/server";
import type { CreateRunResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Vercel Cron target — weekdays 07:00 (see `vercel.json`).
 *
 * Vercel sends `Authorization: Bearer $CRON_SECRET`; when `CRON_SECRET` is set
 * the header is verified in constant time, so the endpoint cannot be used by
 * anyone else to start runs.
 */
export async function GET(request: Request): Promise<NextResponse> {
  if (!isAuthorisedCron(request.headers.get("authorization"))) {
    return NextResponse.json({ detail: "Unauthorised." }, { status: 401 });
  }

  try {
    const created = await callApi<CreateRunResponse>("/v1/runs", {
      method: "POST",
      body: { tickers: DEFAULT_WATCHLIST, mode: "standard" },
    });
    return NextResponse.json({
      triggered: true,
      run_id: created.run_id,
      tickers: created.tickers,
      note: "Brief will pause at the human approval gate before any delivery.",
    });
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "Could not trigger the run.";
    // A duplicate active run is an expected outcome, not an incident.
    return NextResponse.json({ triggered: false, detail }, { status: status === 409 ? 200 : status });
  }
}

export const POST = GET;
