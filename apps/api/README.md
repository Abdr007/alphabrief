# AlphaBrief API

FastAPI + LangGraph supervisor-pattern multi-agent service with an MCP tool server,
deterministic numeric verification and a human-in-the-loop approval gate.

Full documentation lives in the repository root `README.md`. This file exists so the
package metadata (`pyproject.toml` → `readme`) resolves during an editable install.

```
app/
  core/        settings, Claude model router, Langfuse tracing, budget guard, SSE bus, auth
  mcp_server/  MCP tools: prices, fundamentals, metrics, rss_news
  graph/       state, supervisor, data_agent, news_agent, writer, verify, gate
  models/      Pydantic brief schema + SQLAlchemy tables
  services/    repository (Neon Postgres), SMTP delivery
  api/         HTTP surface: POST /v1/runs, SSE stream, approval endpoints
tests/         loop-cap, reducer-merge, verifier, graceful-degradation, security
```

Run locally:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 7860 --reload
```
