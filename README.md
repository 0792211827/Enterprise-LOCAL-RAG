# Enterprise Local RAG

A self-hosted, **local-first** Enterprise RAG-as-a-Service platform. It ingests
documents, indexes them for hybrid (BM25 + vector) retrieval, and answers
questions with a local LLM — including an **agentic RAG** workflow built on
LangGraph. Everything runs locally with **no external AI APIs** required.

## ✨ Features

- **Document ingestion** — PDF parsing (Docling), section-aware chunking, and
  hybrid indexing into OpenSearch.
- **Hybrid retrieval** — BM25 keyword search + dense vector search fused with
  Reciprocal Rank Fusion (RRF).
- **RAG question answering** — standard and streaming responses with citations.
- **Agentic RAG** — LangGraph agent with query rewriting, document grading,
  guardrails, and out-of-scope handling.
- **Admin Control Plane** — Next.js 14 UI to manage knowledge bases, documents,
  applications, providers, retrieval profiles, and system health.
- **Local-first providers** — embeddings (HuggingFace / Ollama /
  OpenAI-compatible), LLM (Ollama / OpenAI-compatible), optional VLM.
- **Observability** — self-hosted Langfuse tracing and Redis caching.

## 🏗️ Architecture

```
                ┌─────────────────────────┐
                │  Admin Control Plane     │  Next.js 14  (:3001)
                │  (frontend/)             │
                └───────────┬─────────────┘
                            │ HTTP
                ┌───────────▼─────────────┐
                │ FastAPI backend (backend/)│  (:8000)
                │  ingest · search · RAG   │
                │  agentic RAG (LangGraph) │
                └─┬───────┬────────┬───────┘
                  │       │        │
        ┌─────────▼─┐ ┌───▼────┐ ┌─▼──────┐ ┌──────────┐
        │PostgreSQL │ │OpenSea │ │ Ollama │ │  Redis   │
        │ metadata  │ │ hybrid │ │  LLM   │ │  cache   │
        │  (:5432)  │ │(:9200) │ │(:11434)│ │ (:6379)  │
        └───────────┘ └────────┘ └────────┘ └──────────┘
                          Langfuse observability (:3000)
```

## 🧩 Services & Ports

| Service | URL | Purpose |
|---------|-----|---------|
| **API** | http://localhost:8000 | FastAPI backend |
| **API docs** | http://localhost:8000/docs | Interactive OpenAPI UI |
| **Admin Control Plane** | http://localhost:3001 | Next.js admin frontend |
| **Langfuse** | http://localhost:3000 | RAG tracing & monitoring |
| **OpenSearch** | http://localhost:9200 | Hybrid search engine |
| **OpenSearch Dashboards** | http://localhost:5601 | Search engine UI |
| **Ollama** | http://localhost:11434 | Local LLM / embeddings runtime |
| **PostgreSQL** | localhost:5432 | Application metadata |
| **Redis** | localhost:6379 | Response cache |

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- [`uv`](https://github.com/astral-sh/uv) (for local Python development)
- Node.js 20+ (for local frontend development)

### Run the full stack

```bash
# 1. Configure environment
cp .env.example .env        # defaults are local-first and work out of the box

# 2. Build and start all services
make start                  # docker compose up --build -d

# 3. Check health
make health
```

Pull a local model into Ollama the first time (defaults to `llama3.2:1b`):

```bash
docker exec -it rag-ollama ollama pull llama3.2:1b
```

Then open the Admin Control Plane at **http://localhost:3001** and the API docs
at **http://localhost:8000/docs**.

### Useful Make targets

```bash
make start     # build + start all services
make stop      # stop services
make logs      # tail logs
make health    # check API / OpenSearch / Ollama
make test      # run backend tests (cd backend && uv run python -m pytest)
make lint      # ruff + mypy
make frontend-dev    # frontend dev server on :3001
make frontend-build  # frontend production build + type check
make clean     # tear down + prune
```

## 🔌 API Overview

All application routes are served under `/api/v1` (search under `/hybrid-search`).

| Area | Route prefix |
|------|--------------|
| Health | `GET /health` |
| Hybrid search | `/hybrid-search` |
| Ask / Streaming RAG | `/api/v1/ask`, `/api/v1/stream` |
| Agentic RAG | `/api/v1` (`agentic-rag`) |
| Knowledge bases | `/api/v1/knowledge-bases` |
| Documents | `/api/v1/documents` |
| Applications | `/api/v1/applications` |
| Providers | `/api/v1/providers` |
| Retrieval configs | `/api/v1/retrieval-configurations` |
| System | `/api/v1/system` |

Full, interactive documentation is available at `/docs`.

## 🖥️ Admin Control Plane (Frontend)

A Next.js 14 admin UI (`frontend/`) for managing knowledge bases, document
ingestion, RAG applications (with a retrieval playground), model providers,
retrieval profiles, and system health.

Run with Docker (part of `make start`):

```bash
docker compose up -d frontend   # serves on http://localhost:3001
```

Local development:

```bash
cd frontend
npm install
npm run dev          # http://localhost:3001
npm run build        # production build + type check
```

The browser calls the API at `http://localhost:8000` (configurable via the
`NEXT_PUBLIC_API_URL` build arg in `compose.yml`). The backend enables CORS for
`localhost:3000/3001` out of the box.

## ⚙️ Configuration

All configuration is environment-driven; see `.env.example` for the full,
documented list. Key groups:

- **Embeddings** (`EMBEDDING__*`) — provider, model, dimension, device.
- **LLM** (`LLM__*`) — provider, endpoint, model, timeout.
- **VLM** (`VLM__*`) — optional vision-language model (off by default).
- **OpenSearch** (`OPENSEARCH__*`) — index and hybrid-search settings.
- **Chunking** (`CHUNKING__*`) — chunk size, overlap, section-based mode.
- **Redis** (`REDIS__*`) and **Langfuse** (`LANGFUSE_*`).

## 🧪 Testing

```bash
cd backend
uv sync                                 # install dependencies
uv run python -m pytest                 # run the backend test suite
uv run python -m pytest --ignore=tests/integration   # offline only
uv run python -m pytest --cov=src       # with coverage

cd frontend && npm run build            # frontend production build + type check
```

Or from the repo root: `make test`, `make frontend-build`.

## 📁 Project Structure

```
.
├── compose.yml              # Full local stack (API, frontend, datastores, Langfuse)
├── Makefile                 # Common developer commands
├── .env / .env.example      # Shared config for compose and the backend
├── backend/                 # FastAPI backend
│   ├── Dockerfile           # Backend image
│   ├── pyproject.toml       # Backend dependencies (managed by uv)
│   ├── uv.lock
│   ├── .env.test            # Test-only env, loaded by pytest-env
│   ├── src/
│   │   ├── main.py          # App entrypoint & router wiring
│   │   ├── config.py        # Settings
│   │   ├── routers/         # HTTP endpoints
│   │   ├── services/        # Ingestion, indexing, retrieval, agents, providers
│   │   ├── repositories/    # Data access
│   │   ├── models/          # SQLAlchemy models
│   │   └── schemas/         # Pydantic schemas
│   └── tests/               # Backend test suite
└── frontend/                # Next.js 14 Admin Control Plane
    ├── Dockerfile
    ├── package.json
    └── src/app/             # App Router pages
```

## 📄 License

MIT — see [LICENSE](LICENSE).
