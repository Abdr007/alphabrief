SHELL := /bin/bash
PY    := apps/api/.venv/bin/python
WEB   := apps/web

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup ---
.PHONY: install
install: ## Create the venv and install API + web dependencies
	cd apps/api && uv venv --python 3.12 .venv
	cd apps/api && uv pip install --python .venv/bin/python -e ".[dev]"
	cd $(WEB) && npm install

# ------------------------------------------------------------------- run ----
.PHONY: api
api: ## Run the API on :7877 with reload
	cd apps/api && .venv/bin/uvicorn app.main:app --port 7877 --env-file ../../.env --reload

.PHONY: web
web: ## Run the Next.js console on :3001
	cd $(WEB) && npm run dev -- --port 3001

.PHONY: mcp
mcp: ## Run the MCP tool server standalone over stdio
	cd apps/api && .venv/bin/python -m app.mcp_server

# ---------------------------------------------------------------- quality ---
.PHONY: lint
lint: ## ruff check + format check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

.PHONY: fix
fix: ## Apply ruff fixes and formatting
	$(PY) -m ruff check . --fix
	$(PY) -m ruff format .

.PHONY: types
types: ## mypy --strict over app, tests and eval
	$(PY) -m mypy

.PHONY: test
test: ## Run the pytest suite
	cd apps/api && .venv/bin/python -m pytest tests/ -q

.PHONY: test-offline
test-offline: ## Run only the tests that need no network
	cd apps/api && .venv/bin/python -m pytest tests/ -q -m "not network"

.PHONY: web-lint
web-lint: ## eslint + tsc + production build
	cd $(WEB) && npm run lint
	cd $(WEB) && npm run typecheck
	cd $(WEB) && npm run build

.PHONY: check
check: lint types test web-lint ## Every quality gate
	@echo ""
	@echo "  ✓ ruff · ruff-format · mypy --strict · pytest · eslint · tsc · next build"

# ------------------------------------------------------------------- eval ---
.PHONY: eval
eval: ## 20 scored runs -> eval/results.md
	$(PY) eval/run_eval.py --runs 20

.PHONY: diagram
diagram: ## Print the mermaid graph, generated from the real StateGraph
	@$(PY) -c "import sys; sys.path.insert(0,'apps/api'); from app.graph.build import mermaid_diagram; print(mermaid_diagram())"

# ------------------------------------------------------------------ infra ---
.PHONY: docker
docker: ## Build the API container
	docker build -t alphabrief-api -f apps/api/Dockerfile apps/api

.PHONY: docker-run
docker-run: ## Run the API container, published on :7877
	docker run --rm -p 7877:7860 --env-file .env alphabrief-api

.PHONY: clean
clean: ## Remove caches and local databases
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/api/.pytest_cache apps/api/.mypy_cache .mypy_cache .ruff_cache
	rm -f apps/api/*.db alphabrief.db
