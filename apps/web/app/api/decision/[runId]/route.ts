import { NextResponse } from "next/server";

import { ApiError, callApi } from "@/lib/server";

export const dynamic = "force-dynamic";

const RUN_ID = /^run_[a-z0-9]{4,40}$/;
const ACTIONS = new Set(["approve", "reject", "edit"]);
const MAX_NOTE = 1000;
const MAX_EDIT = 2000;

interface DecisionResult {
  run_id: string;
  status: string;
  action: string;
}

/**
 * Approve / edit / reject a brief.
 *
 * The approval bearer token lives only in this server process. A browser can
 * reach this route, but it cannot obtain the credential that authorises the
 * underlying API call.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  const { runId } = await context.params;
  if (!RUN_ID.test(runId)) {
    return NextResponse.json({ detail: "Invalid run id." }, { status: 400 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Body must be JSON." }, { status: 400 });
  }

  const body = (payload ?? {}) as Record<string, unknown>;
  const action = typeof body.action === "string" ? body.action : "";
  if (!ACTIONS.has(action)) {
    return NextResponse.json(
      { detail: "action must be one of approve, reject, edit." },
      { status: 400 },
    );
  }

  const outbound: Record<string, unknown> = {
    action,
    reviewer: trim(body.reviewer, 128) ?? "analyst",
  };
  const note = trim(body.note, MAX_NOTE);
  if (note) outbound.note = note;
  if (action === "edit") {
    const headline = trim(body.edited_headline, MAX_EDIT);
    const summary = trim(body.edited_summary, MAX_EDIT);
    if (headline) outbound.edited_headline = headline;
    if (summary) outbound.edited_summary = summary;
  }

  try {
    const result = await callApi<DecisionResult>(`/v1/runs/${runId}/decision`, {
      method: "POST",
      body: outbound,
      authenticated: true,
      timeoutMs: 60_000,
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "Could not record the decision.";
    return NextResponse.json({ detail }, { status });
  }
}

function trim(value: unknown, max: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.trim();
  return cleaned ? cleaned.slice(0, max) : null;
}
