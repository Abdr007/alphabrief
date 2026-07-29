import { NextResponse } from "next/server";

import { ApiError, callApi } from "@/lib/server";

export const dynamic = "force-dynamic";

interface Health {
  status: string;
  version: string;
  engine: string;
  mcp_transport: string;
  langfuse: boolean;
  smtp: boolean;
  database: string;
  watchlist: string[];
  models: Record<string, string>;
}

/** Surface the API's configuration in the console header. */
export async function GET(): Promise<NextResponse> {
  try {
    const health = await callApi<Health>("/health", { timeoutMs: 6_000 });
    return NextResponse.json(health);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    const detail = error instanceof Error ? error.message : "API unreachable.";
    return NextResponse.json({ status: "unreachable", detail }, { status });
  }
}
