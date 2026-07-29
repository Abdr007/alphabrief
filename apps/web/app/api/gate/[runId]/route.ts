import { NextResponse } from "next/server";

import { ApiError, callApi } from "@/lib/server";
import type { GatePayload } from "@/lib/types";

export const dynamic = "force-dynamic";

const RUN_ID = /^run_[a-z0-9]{4,40}$/;

/** The approval view: brief plus the full verification report. */
export async function GET(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  const { runId } = await context.params;
  if (!RUN_ID.test(runId)) {
    return NextResponse.json({ detail: "Invalid run id." }, { status: 400 });
  }
  try {
    const gate = await callApi<GatePayload>(`/v1/runs/${runId}/gate`);
    return NextResponse.json(gate);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "Could not load the brief.";
    return NextResponse.json({ detail }, { status });
  }
}
