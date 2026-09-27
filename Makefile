.PHONY: help start stop restart status logs health setup format lint test test-cov \
        frontend-setup frontend-dev frontend-build clean audit-purge

# The virtualenv lives at the repo root, not in backend/. uv resolves this
# relative to the project root (backend/, where pyproject.toml is).
export UV_PROJECT_ENVIRONMENT := ../.venv

# Default target
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# Service management
start: ## Start all services
	docker compose up --build -d

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
API_PORT ?= 8000

# curl -f fails on HTTP errors and connection refusals; capturing its output
# first (rather than piping to jq, which exits 0 on empty input) makes the
# "not responding" fallback actually fire.
health: ## Check all services health
	@echo "Checking service health..."
	@out=$$(curl -sf http://localhost:$(API_PORT)/api/v1/health) && echo "$$out" | jq . || echo "API not responding"
	@out=$$(curl -sf http://localhost:9200/_cluster/health) && echo "$$out" | jq . || echo "OpenSearch not responding"
	@out=$$(curl -sf http://localhost:11434/api/version) && echo "$$out" | jq . || echo "Ollama not responding"

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

test-cov: ## Run tests with coverage
	cd backend && uv run python -m pytest --cov=src --cov-report=html

# Audit retention. Runs inside the rag-api container, against its database.
# DAYS defaults to AUDIT__RETENTION_DAYS; DRY_RUN=1 only counts.
#   make audit-purge DAYS=365 DRY_RUN=1
audit-purge: ## Delete audit events older than DAYS (or AUDIT__RETENTION_DAYS)
	docker exec rag-api python -m src.cli.audit_purge $(if $(DAYS),--days $(DAYS)) $(if $(DRY_RUN),--dry-run)

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