"""Model Context Protocol tool server.

All market, news and metric capability is exposed as MCP tools, so the tools are
standardised, discoverable and reusable by any agent framework — not just this
LangGraph app.
"""

from app.mcp_server.registry import TOOL_NAMES, build_server

__all__ = ["TOOL_NAMES", "build_server"]
