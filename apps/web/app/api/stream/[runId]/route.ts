import { NextResponse } from "next/server";

import { openStream } from "@/lib/server";

export const dynamic = "force-dynamic";
// Node runtime: the upstream body is piped through unchanged, unbuffered.
export const runtime = "nodejs";

const RUN_ID = /^run_[a-z0-9]{4,40}$/;

/** Proxy the API's Server-Sent Events stream, so the browser needs no CORS. */
export async function GET(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await context.params;
  if (!RUN_ID.test(runId)) {
    return NextResponse.json({ detail: "Invalid run id." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await openStream(runId);
  } catch {
    return NextResponse.json({ detail: "Cannot reach the AlphaBrief API." }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { detail: `Upstream stream unavailable (${upstream.status}).` },
      { status: upstream.status || 502 },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
