# AlphaBrief API — FastAPI + LangGraph + the MCP tool server in one container.
#
# One image serves three things that are usually three deployments: the HTTP
# surface, the agent orchestrator, and the MCP server the agents call (spawned
# in-container over stdio). That is what makes the free tiers viable — a single
# container on Cloud Run's always-free allowance, or a Hugging Face Docker Space
# on port 7860 with no card at all.
#
#   docker build -t alphabrief-api -f apps/api/Dockerfile apps/api
#   docker run --rm -p 7860:7860 --env-file .env alphabrief-api

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is used by the container healthcheck only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------- deps ------
FROM base AS deps
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# --------------------------------------------------------------- runtime ----
FROM base AS runtime

# Hugging Face Spaces runs containers as uid 1000; matching it here means the
# same image works on Spaces and Cloud Run without a rebuild.
RUN useradd --create-home --uid 1000 alphabrief

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

WORKDIR /home/alphabrief/app
COPY --chown=alphabrief:alphabrief pyproject.toml README.md ./
COPY --chown=alphabrief:alphabrief app ./app

USER alphabrief

ENV PORT=7860 \
    PYTHONPATH=/home/alphabrief/app \
    HOME=/home/alphabrief \
    MCP_TRANSPORT=stdio \
    DATABASE_URL=sqlite:////home/alphabrief/alphabrief.db

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Cloud Run injects $PORT; Spaces expects 7860. Honour whichever is set.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1 --timeout-keep-alive 75"]
