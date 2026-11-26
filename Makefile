.PHONY: help install infra app app-stop backend frontend pull-model psql start stop restart status logs health setup \
        format lint test test-telemetry-off test-cov frontend-setup frontend-dev frontend-build clean audit-purge chat-purge uploads-cleanup \
        reindex eval eval-compare start-hardened health-hardened telemetry-start telemetry-start-hardened telemetry-stop connectors connector-sync ingest-samples

# Task runner for Enterprise-LOCAL-RAG.
#
#   make            list every target
#   make install    one-time setup: Python + frontend deps, .env from .env.example
#   make start      the whole stack in Docker (api, frontend, datastores, Langfuse)
#   make app        dev mode: datastores in Docker, backend + frontend on the host with reload
#
# Kept compatible with the GNU Make 3.81 that ships with macOS: any recipe
# needing shell logic is one joined command, not several lines.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# The virtualenv lives at the repo root, not in backend/. uv resolves this
# relative to the project root (backend/, where pyproject.toml is).
export UV_PROJECT_ENVIRONMENT := ../.venv

# Host ports. API_PORT is also what `make health` probes; with compose.localports.yml
# the container is on 8020, so pass API_PORT=8020 to health, not to app.
API_PORT ?= 8010
UI_PORT  ?= 3003
PG_PORT  ?= 5432

# Datastores and model server that `make app` needs; the api and frontend run on the host instead.
INFRA ?= postgres opensearch redis ollama
MODEL ?= $(or $(shell sed -n 's/^OLLAMA_MODEL=//p' .env 2>/dev/null | tail -1),llama3.2:1b)

# .env addresses the compose network (postgres, opensearch, ollama...). A host process reaches
# the published ports on localhost, so these are overridden in its environment only; the
# provider endpoints are rewritten just when they still point at the bundled ollama.
LOCAL_ENV = export POSTGRES_DATABASE_URL=postgresql://rag_user:rag_password@localhost:$(PG_PORT)/rag_db \
	OPENSEARCH__HOST=http://localhost:9200 OPENSEARCH_HOST=http://localhost:9200 \
	OLLAMA_HOST=http://localhost:11434 REDIS__HOST=localhost LANGFUSE__HOST=http://localhost:3000 \
	CORS_ALLOW_ORIGINS=http://localhost:$(UI_PORT),http://127.0.0.1:$(UI_PORT),http://localhost:3001,http://localhost:3000; \
	for v in LLM__ENDPOINT EMBEDDING__ENDPOINT VLM__ENDPOINT; do \
	  val=$$(sed -n "s/^$$v=//p" .env | tail -1); \
	  case "$$val" in *//ollama:*) export $$v=$${val/\/\/ollama:/\/\/localhost:};; esac; \
	done

# Default target
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# Setup
install: setup frontend-setup ## One-time setup: Python + frontend deps, and .env from .env.example if missing
	@if [ ! -f .env ]; then cp .env.example .env; echo "wrote .env -- review it before 'make start' or 'make app'"; fi

# Local development: datastores in Docker, the two servers on the host.
infra: ## Start only the datastores and Ollama (what 'make app' needs), waiting until healthy
	docker compose up -d --wait $(INFRA)

pull-model: ## Pull the Ollama chat model into rag-ollama (MODEL=llama3.2:1b)
	docker exec rag-ollama ollama pull $(MODEL)

# One joined command so a single trap reaps both servers however the run ends -- by
# port too, since uvicorn --reload and next dev run as children of the shell we start.
app: ## Dev stack: datastores in Docker, backend (reload) + frontend on the host
	@if [ ! -f .env ]; then echo "no .env -- run 'make install' first"; exit 1; fi; \
	for p in $(API_PORT) $(UI_PORT); do \
	  if lsof -nP -iTCP:$$p -sTCP:LISTEN -t >/dev/null 2>&1; then \
	    echo "port $$p is in use -- 'make app-stop' frees it"; exit 1; fi; \
	done; \
	echo '==> datastores'; \
	docker compose up -d --wait $(INFRA) || exit 1; \
	$(LOCAL_ENV); \
	echo '==> backend on $(API_PORT)'; \
	(cd backend && uv run uvicorn src.main:app --reload --port $(API_PORT)) & api=$$!; \
	trap 'kill $$api $$ui 2>/dev/null; sleep 1; \
	  for p in $(API_PORT) $(UI_PORT); do \
	    held=$$(lsof -nP -iTCP:$$p -sTCP:LISTEN -t 2>/dev/null); \
	    if [ -n "$$held" ]; then kill $$held 2>/dev/null; fi; \
	  done' EXIT INT TERM; \
	for _ in $$(seq 120); do \
	  curl -sf "http://127.0.0.1:$(API_PORT)/api/v1/health" >/dev/null && break; \
	  kill -0 $$api 2>/dev/null || { echo 'backend exited during startup -- see above'; exit 1; }; \
	  sleep 1; \
	done; \
	echo '==> frontend on $(UI_PORT)'; \
	(cd frontend && NEXT_PUBLIC_API_URL=http://localhost:$(API_PORT) npm run dev -- -p $(UI_PORT)) & ui=$$!; \
	wait $$ui

backend: ## Backend only, with reload, against 'make infra' datastores
	@$(LOCAL_ENV); cd backend && uv run uvicorn src.main:app --reload --port $(API_PORT)

frontend: ## Frontend only, against the backend on API_PORT
	cd frontend && NEXT_PUBLIC_API_URL=http://localhost:$(API_PORT) npm run dev -- -p $(UI_PORT)

app-stop: ## Free the dev ports (API_PORT, UI_PORT) and stop the datastores 'make app' started
	@for p in $(API_PORT) $(UI_PORT); do \
	  pids=$$(lsof -nP -iTCP:$$p -sTCP:LISTEN -t 2>/dev/null); \
	  if [ -n "$$pids" ]; then kill $$pids 2>/dev/null; echo "freed $$p"; fi; \
	done
	-@docker compose stop $(INFRA) >/dev/null 2>&1

psql: ## Open psql in the rag-postgres container
	docker exec -it rag-postgres psql -U rag_user -d rag_db

# Service management
start: ## Start all services
	docker compose up --build -d

start-hardened: ## Start with only the API/UIs published, on 127.0.0.1 (see compose.hardened.yml)
	docker compose -f compose.yml -f compose.hardened.yml up --build -d

telemetry-start: ## Start the optional telemetry stack (collector, Prometheus, Tempo, Grafana); see README, "Telemetry"
	docker compose --profile telemetry up -d otel-collector prometheus tempo grafana

telemetry-start-hardened: ## Same, with compose.hardened.yml: only Grafana is published, on RAG_BIND_ADDRESS (loopback by default)
	docker compose -f compose.yml -f compose.hardened.yml --profile telemetry up -d otel-collector prometheus tempo grafana

telemetry-stop: ## Stop and remove the telemetry stack (its data volumes are kept)
	docker compose --profile telemetry rm -sf grafana otel-collector prometheus tempo

stop: ## Stop all services
	docker compose down

restart: ## Restart all services
	docker compose restart

status: ## Show service status
	docker compose ps

logs: ## Show service logs
	docker compose logs -f

# Health checks
# Override when the API's host port is remapped, e.g. `make health API_PORT=8020`
# with compose.localports.yml.

# curl -f fails on HTTP errors and connection refusals; capturing its output
# first (rather than piping to jq, which exits 0 on empty input) makes the
# "not responding" fallback actually fire.
health: ## Check all services health
	@echo "Checking service health..."
	@out=$$(curl -sf http://localhost:$(API_PORT)/api/v1/health) && echo "$$out" | jq . || echo "API not responding"
	@out=$$(curl -sf http://localhost:9200/_cluster/health) && echo "$$out" | jq . || echo "OpenSearch not responding"
	@out=$$(curl -sf http://localhost:11434/api/version) && echo "$$out" | jq . || echo "Ollama not responding"

# The hardened overlay publishes no datastore ports, so OpenSearch and Ollama
# are checked from inside the network, through the API container.
health-hardened: ## Check health of a stack started with start-hardened
	@echo "Checking service health (hardened)..."
	@out=$$(curl -sf http://127.0.0.1:$(API_PORT)/api/v1/health) && echo "$$out" | jq . || echo "API not responding"
	@out=$$(docker exec rag-api python -c "import urllib.request; print(urllib.request.urlopen('http://opensearch:9200/_cluster/health').read().decode())" 2>/dev/null) && echo "$$out" | jq . || echo "OpenSearch not responding"
	@out=$$(docker exec rag-api python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/version').read().decode())" 2>/dev/null) && echo "$$out" | jq . || echo "Ollama not responding"

# Development (backend)
setup: ## Install Python dependencies
	cd backend && uv sync

format: ## Format code
	cd backend && uv run python -m ruff format

lint: ## Lint and type check
	cd backend && uv run python -m ruff check --fix
	cd backend && uv run python -m mypy src/

test: ## Run tests
	cd backend && uv run python -m pytest

test-telemetry-off: ## Run the API tests with telemetry off (answers must not change)
	cd backend && RAG_TEST_TELEMETRY=off uv run python -m pytest tests/api

test-cov: ## Run tests with coverage
	cd backend && uv run python -m pytest --cov=src --cov-report=html

# Audit retention. Runs inside the rag-api container, against its database.
# DAYS defaults to AUDIT__RETENTION_DAYS; DRY_RUN=1 only counts.
#   make audit-purge DAYS=365 DRY_RUN=1
audit-purge: ## Delete audit events older than DAYS (or AUDIT__RETENTION_DAYS)
	docker exec rag-api python -m src.cli.audit_purge $(if $(DAYS),--days $(DAYS)) $(if $(DRY_RUN),--dry-run)

# Chat retention. The API purges daily on its own; this runs it on demand.
# DAYS defaults to CHAT__RETENTION_DAYS; DRY_RUN=1 only counts.
#   make chat-purge DAYS=30 DRY_RUN=1
chat-purge: ## Delete Conversations idle longer than DAYS (or CHAT__RETENTION_DAYS)
	docker exec rag-api python -m src.cli.chat_purge $(if $(DAYS),--days $(DAYS)) $(if $(DRY_RUN),--dry-run)

# Rebuild the chunk index on the current mapping (lucene, EMBEDDING__DIMENSION) and swap the alias. Explicit and
# resumable: run it again after an interruption. Pause ingestion while it runs (see docs/adr/0001).
#   make reindex DRY_RUN=1
#   make reindex RE_EMBED=1 KEEP_OLD=1   (KEEP_OLD retains the previous index; it holds deleted chunks until removed)
reindex: ## Rebuild the chunk index (lucene engine / new embedding model) and swap the alias
	docker exec rag-api python -m src.cli.reindex $(if $(DRY_RUN),--dry-run) $(if $(RE_EMBED),--re-embed) $(if $(KEEP_OLD),--keep-old)

# Upload-area cleanup. Off unless asked for: removes uploaded files older than DAYS
# (default INGESTION__UPLOAD_RETENTION_DAYS) that no queued or processing document needs.
#   make uploads-cleanup DAYS=14 DRY_RUN=1
uploads-cleanup: ## Remove stale files from the upload area older than DAYS
	docker exec rag-api python -m src.cli.upload_cleanup $(if $(DAYS),--days $(DAYS)) $(if $(DRY_RUN),--dry-run)

# Connectors mirror a source folder into a Knowledge Base (see README, "Connectors"). The job runs as its own
# command, in the rag-api container unless you use the `connectors` compose profile.
#   make connectors                       list them and how their last run went
#   make connector-sync NAME=hr-share     (or ALL=1; DRY_RUN=1 reports without changing anything)
connectors: ## List Connectors and their last run
	docker exec rag-api python -m src.cli.connector list

connector-sync: ## Mirror a Connector's source into its Knowledge Base: NAME=... or ALL=1
	@test -n "$(NAME)$(ALL)" || { echo "usage: make connector-sync NAME=hr-share   (or ALL=1)"; exit 2; }
	docker exec rag-api python -m src.cli.connector sync $(if $(ALL),--all,$(NAME)) $(if $(DRY_RUN),--dry-run) $(if $(ALLOW_EMPTY),--allow-empty)

# Sample-data check: ingests the real sample folder into a scratch Knowledge Base and prints per-file results. Local and
# opt-in, never part of CI (which uses synthetic files). The folder and the questions are copied into the rag-api container.
#   make ingest-samples              (SAMPLES=<folder>; URLS=1 adds the links in Links.docx; ASK=1 asks the sample questions)
#   make ingest-samples CLEAN=1      removes the scratch Knowledge Base
SAMPLES ?= data/File Examples
ingest-samples: ## Ingest the sample folder into a scratch Knowledge Base and report each file (URLS=1, ASK=1, CLEAN=1)
	@if [ -n "$(CLEAN)" ]; then docker exec rag-api python -m src.cli.ingest_samples --clean; exit $$?; fi; \
	test -d "$(SAMPLES)" || { echo "no sample folder at '$(SAMPLES)' (SAMPLES=<folder>)"; exit 2; }; \
	docker exec rag-api rm -rf /tmp/sample-data /tmp/sample-data-questions.yaml && \
	docker cp "$(SAMPLES)" rag-api:/tmp/sample-data && \
	docker cp evals/sample-data-questions.yaml rag-api:/tmp/sample-data-questions.yaml && \
	docker exec rag-api python -m src.cli.ingest_samples --folder /tmp/sample-data --questions /tmp/sample-data-questions.yaml \
		$(if $(URLS),--urls) $(if $(ASK),--ask)

# Eval harness. Runs in the rag-api container: the Golden set is copied in and the
# report copied back to REPORT (default eval-report.json). The exit status is the
# command's: non-zero only when a must_not_return case leaked (see docs/evaluation.md).
#   make eval GOLDEN=evals/example-golden-set.yaml APP=hr-assistant
#   make eval GOLDEN=... APP=... K=5 RETRIEVAL_ONLY=1 GROUNDEDNESS=1 REPORT=before.json
eval: ## Score APP against the Golden set GOLDEN
	@test -n "$(GOLDEN)" -a -n "$(APP)" || { echo "usage: make eval GOLDEN=path.yaml APP=slug"; exit 2; }
	docker cp $(GOLDEN) rag-api:/tmp/golden.yaml
	@docker exec rag-api python -m src.cli.eval run /tmp/golden.yaml --application $(APP) --out /tmp/eval-report.json \
		$(if $(K),--k $(K)) $(if $(RETRIEVAL_ONLY),--retrieval-only) $(if $(GROUNDEDNESS),--groundedness); \
	status=$$?; docker cp rag-api:/tmp/eval-report.json $(or $(REPORT),eval-report.json) && exit $$status

eval-compare: ## Compare two eval reports: BEFORE=a.json AFTER=b.json
	@test -n "$(BEFORE)" -a -n "$(AFTER)" || { echo "usage: make eval-compare BEFORE=a.json AFTER=b.json"; exit 2; }
	cd backend && uv run python -m src.cli.eval compare ../$(BEFORE) ../$(AFTER)

# Development (frontend)
frontend-setup: ## Install frontend dependencies
	cd frontend && npm install

frontend-dev: ## Run the frontend dev server on :3001
	cd frontend && npm run dev

frontend-build: ## Production build + type check
	cd frontend && npm run build

# Cleanup
clean: ## Clean up everything
	docker compose down -v
	docker system prune -f