"""Entry point so the MCP server can be spoken to over stdio.

    python -m app.mcp_server

This is what the agents spawn when ``MCP_TRANSPORT=stdio`` (the default), and
what any other MCP-capable client — Claude Desktop, another agent framework —
would point at to reuse these tools.
"""

from __future__ import annotations

import logging
import os
import sys

from app.mcp_server.providers import ProviderContext
from app.mcp_server.registry import build_server


def main() -> None:
    # stdout IS the MCP transport, so logging must go to stderr. Stray writes to
    # fd 1 are already handled by the SDK: `stdio_server()` claims fd 1 for the
    # wire and repoints the process's stdout at stderr for the duration, so a
    # chatty dependency cannot corrupt JSON-RPC framing. Do NOT reassign
    # `sys.stdout` here — the SDK needs the real handle to claim.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    interval = float(os.environ.get("PROVIDER_MIN_INTERVAL_SECONDS", "0.20"))
    server = build_server(ProviderContext(min_interval_seconds=interval))
    server.run("stdio")


if __name__ == "__main__":
    main()
