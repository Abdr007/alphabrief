import { NextResponse } from "next/server";

import { ApiError, callApi } from "@/lib/server";
import type { RunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Past runs with cost, latency and iteration count. */
export async function GET(request: Request): Promise<NextResponse> {
  const requested = Number(new URL(request.url).searchParams.get("limit") ?? "40");
  const limit = Number.isFinite(requested) ? Math.min(Math.max(Math.trunc(requested), 1), 100) : 40;
  try {
    const runs = await callApi<RunSummary[]>(`/v1/runs?limit=${limit}`);
    return NextResponse.json(runs);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "Could not load the archive.";
    return NextResponse.json({ detail }, { status });
  }
}
