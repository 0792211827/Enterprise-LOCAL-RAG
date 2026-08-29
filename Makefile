.PHONY: help start stop restart status logs health setup format lint test test-cov \
        frontend-setup frontend-dev frontend-build clean

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
health: ## Check all services health
	@echo "Checking service health..."
	@curl -s http://localhost:8000/health | jq . || echo "API not responding"
	@curl -s http://localhost:9200/_cluster/health | jq . || echo "OpenSearch not responding"
	@curl -s http://localhost:11434/api/version | jq . || echo "Ollama not responding"

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