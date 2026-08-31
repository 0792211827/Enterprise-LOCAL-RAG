import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config import get_settings
from src.db.factory import make_database
from src.routers import (
    agentic_ask,
    api_keys,
    applications,
    chat_completions,
    documents,
    hybrid_search,
    knowledge_bases,
    ping,
    providers,
    retrieval,
    system,
)
from src.routers.ask import ask_router, stream_router
from src.services.cache.factory import make_cache_client
from src.services.embeddings.factory import make_embeddings_service
from src.services.langfuse.factory import make_langfuse_tracer
from src.services.ollama.factory import make_ollama_client
from src.services.opensearch.factory import make_opensearch_client
from src.services.pdf_parser.factory import make_pdf_parser_service
from src.services.providers.llm.factory import make_llm_provider
from src.services.providers.vlm.factory import make_vlm_provider

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan for the API.
    """
    logger.info("Starting RAG API...")

    settings = get_settings()
    app.state.settings = settings

    database = make_database()
    app.state.database = database
    logger.info("Database connected")

    # Initialize search service
    opensearch_client = make_opensearch_client()
    app.state.opensearch_client = opensearch_client

    # Verify OpenSearch connectivity and create index if needed
    if opensearch_client.health_check():
        logger.info("OpenSearch connected successfully")

        # Setup hybrid index (supports all search types)
        setup_results = opensearch_client.setup_indices(force=False)
        if setup_results.get("hybrid_index"):
            logger.info("Hybrid index created")
        else:
            logger.info("Hybrid index already exists")

        # Get simple statistics
        try:
            stats = opensearch_client.client.count(index=opensearch_client.index_name)
            logger.info(f"OpenSearch ready: {stats['count']} documents indexed")
        except Exception:
            logger.info("OpenSearch index ready (stats unavailable)")
    else:
        logger.warning("OpenSearch connection failed - search features will be limited")

    # Initialize platform services (local-first, provider-based).
    app.state.pdf_parser = make_pdf_parser_service()
    app.state.embeddings_service = make_embeddings_service()
    app.state.ollama_client = make_ollama_client()
    app.state.llm_provider = make_llm_provider(settings)
    app.state.vlm_provider = make_vlm_provider(settings)
    app.state.langfuse_tracer = make_langfuse_tracer()
    app.state.cache_client = make_cache_client(settings)
    logger.info(
        "Services initialized: PDF parser, OpenSearch, Embeddings (%s), LLM (%s), VLM (%s), Langfuse, Cache",
        settings.embedding.provider,
        settings.llm.provider,
        "enabled" if app.state.vlm_provider else "disabled",
    )

    logger.info("API ready")
    yield

    # Cleanup
    database.teardown()
    logger.info("API shutdown complete")


app = FastAPI(
    title="Enterprise Local RAG API",
    description="Self-hosted, local-first enterprise RAG-as-a-Service platform",
    version=os.getenv("APP_VERSION", "0.1.0"),
    lifespan=lifespan,
)

# CORS for the Admin Control Plane frontend. Origins are configurable via the
# CORS_ALLOW_ORIGINS environment variable (comma-separated); defaults cover the
# local dev + docker-compose frontend.
_cors_origins = os.getenv(
    "CORS_ALLOW_ORIGINS",
    "http://localhost:3001,http://localhost:3000,http://127.0.0.1:3001",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(ping.router, prefix="/api/v1")  # Health check endpoint
app.include_router(hybrid_search.router, prefix="/api/v1")  # Search chunks with BM25/hybrid
app.include_router(ask_router, prefix="/api/v1")  # RAG question answering with LLM
app.include_router(stream_router, prefix="/api/v1")  # Streaming RAG responses
app.include_router(agentic_ask.router)  # Agentic RAG with intelligent retrieval

# Admin Control Plane domain routers.
app.include_router(knowledge_bases.router)
app.include_router(documents.router)
app.include_router(applications.router)
app.include_router(providers.router)
app.include_router(retrieval.router)
app.include_router(system.router)
app.include_router(api_keys.router)

# Mounted at bare /v1 (not /api/v1) so stock OpenAI SDK clients work unmodified:
# they append /chat/completions to a base_url ending in /v1.
app.include_router(chat_completions.router)


if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="0.0.0.0")
