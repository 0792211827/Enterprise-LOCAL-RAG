import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from src.auth import Role, make_authenticator, require_role
from src.config import get_settings
from src.db.factory import make_database
from src.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from src.middleware.audit import AuditMiddleware
from src.routers import (
    agentic_ask,
    api_keys,
    applications,
    audit,
    auth,
    chat,
    chat_admin,
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
from src.services.audit import AUDIT_UNAVAILABLE_MESSAGE, AuditWriteError, make_audit_recorder
from src.services.cache.factory import make_cache_client
from src.services.chat import run_daily
from src.services.embeddings.factory import make_embeddings_service
from src.services.langfuse.factory import make_langfuse_tracer
from src.services.ollama.factory import make_ollama_client
from src.services.opensearch.dimension_guard import check_index_dimension
from src.services.opensearch.factory import make_opensearch_client
from src.services.pdf_parser.factory import make_pdf_parser_service
from src.services.providers.llm.factory import make_llm_provider, make_provider_registry
from src.services.providers.vlm.factory import make_vlm_provider
from src.services.rerank.factory import make_reranker

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

    # Built first: an incomplete auth configuration must stop startup before
    # anything else is initialised, not surface as 500s on the first request.
    app.state.authenticator = make_authenticator(settings)
    logger.info("Authentication mode: %s", settings.auth.mode)

    database = make_database()
    app.state.database = database
    logger.info("Database connected")

    app.state.audit_recorder = make_audit_recorder(settings, database)
    logger.info("Audit logging: %s (fail mode %s)", settings.audit.level, settings.audit.fail_mode)

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
        # Ingestion is refused (409) while the existing index disagrees with
        # EMBEDDING__DIMENSION; search and the rest of the API stay up.
        app.state.ingestion_guard = check_index_dimension(opensearch_client, settings.embedding.dimension)
        if app.state.ingestion_guard is None:
            logger.info("Chunk index dimension agrees with configuration")
    else:
        logger.warning("OpenSearch connection failed - search features will be limited")

    # Initialize platform services (local-first, provider-based).
    app.state.pdf_parser = make_pdf_parser_service()
    app.state.embeddings_service = make_embeddings_service()
    app.state.ollama_client = make_ollama_client()
    app.state.llm_provider = make_llm_provider(settings)
    app.state.provider_registry = make_provider_registry(settings)
    app.state.reranker = make_reranker(settings)
    app.state.vlm_provider = make_vlm_provider(settings)
    app.state.langfuse_tracer = make_langfuse_tracer()
    app.state.cache_client = make_cache_client(settings)
    logger.info(
        "Services initialized: PDF parser, OpenSearch, Embeddings (%s), LLM (%s), VLM (%s), Langfuse, Cache",
        settings.embedding.provider,
        settings.llm.provider,
        "enabled" if app.state.vlm_provider else "disabled",
    )
    logger.info("Reranker: %s", "enabled" if app.state.reranker else "disabled")

    purge_task = None
    if settings.chat.retention_days > 0:
        purge_task = asyncio.create_task(run_daily(database.get_session, settings.chat.retention_days))
        logger.info("Chat retention: Conversations are purged %d days after their last message", settings.chat.retention_days)
    else:
        logger.info("Chat retention: Conversations are kept forever (CHAT__RETENTION_DAYS=0)")

    logger.info("API ready")
    yield

    # Cleanup
    if purge_task is not None:
        purge_task.cancel()
        with suppress(asyncio.CancelledError):
            await purge_task
    database.teardown()
    logger.info("API shutdown complete")


app = FastAPI(
    title="Enterprise Local RAG API",
    description="Self-hosted, local-first enterprise RAG-as-a-Service platform",
    version=os.getenv("APP_VERSION", "0.1.0"),
    lifespan=lifespan,
)


@app.exception_handler(AuditWriteError)
async def audit_write_failed(request: Request, exc: AuditWriteError) -> JSONResponse:
    """AUDIT__FAIL_MODE=deny: an answer that cannot be audited is not served."""
    message = AUDIT_UNAVAILABLE_MESSAGE
    if request.url.path.startswith("/v1/"):
        # OpenAI SDK clients only surface errors in this shape.
        return JSONResponse(
            status_code=503,
            content={"error": {"message": message, "type": "server_error", "param": None, "code": "audit_unavailable"}},
        )
    return JSONResponse(status_code=503, content={"detail": message})


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
    expose_headers=[REQUEST_ID_HEADER],
)

# Records admin changes and 401/403s (a no-op with AUDIT__LEVEL=off). Inside
# the request-context middleware, so every event carries the request ID.
app.add_middleware(AuditMiddleware)

# Added last so it is outermost: every response, including CORS preflights and
# errors raised by inner middleware, carries the correlation ID.
app.add_middleware(RequestContextMiddleware, trusted_proxies=get_settings().auth.trusted_proxy_list)

# Route access policy. Applied here, once per router, rather than inside each
# handler, so the whole matrix is visible in one place and a router cannot be
# mounted without a deliberate choice (tests/unit/test_route_policy.py fails on
# any route missing from it). With AUTH__MODE=disabled every check passes.
QUERIER = [Depends(require_role(Role.QUERIER))]
ADMIN = [Depends(require_role(Role.ADMIN))]
AUDITOR = [Depends(require_role(Role.AUDITOR))]

# Public: orchestrator liveness probe.
app.include_router(ping.router, prefix="/api/v1")

# Query surface.
app.include_router(hybrid_search.router, prefix="/api/v1", dependencies=QUERIER)
app.include_router(ask_router, prefix="/api/v1", dependencies=QUERIER)
app.include_router(stream_router, prefix="/api/v1", dependencies=QUERIER)
app.include_router(agentic_ask.router, dependencies=QUERIER)

# Employee chat: the Applications a user may use, streamed answers, source Documents.
app.include_router(chat.router, dependencies=QUERIER)

# Admin Control Plane. Includes the provider /test and /models endpoints, which
# make the backend call caller-supplied URLs and so must never be open.
app.include_router(knowledge_bases.router, dependencies=ADMIN)
app.include_router(documents.router, dependencies=ADMIN)
app.include_router(applications.router, dependencies=ADMIN)
app.include_router(providers.router, dependencies=ADMIN)
app.include_router(retrieval.router, dependencies=ADMIN)
app.include_router(system.router, dependencies=ADMIN)
app.include_router(api_keys.router, dependencies=ADMIN)
app.include_router(chat_admin.router, dependencies=ADMIN)

# Read-only audit trail. Admins hold the auditor role too unless
# AUTH__ADMIN_CAN_READ_AUDIT=false (separation of duties).
app.include_router(audit.router, dependencies=AUDITOR)

# Any authenticated caller: lets users and operators inspect their own mapping.
app.include_router(auth.router)

# Mounted at bare /v1 (not /api/v1) so stock OpenAI SDK clients work unmodified:
# they append /chat/completions to a base_url ending in /v1. Authorised inside
# the handler, since it accepts application API keys as well as user tokens.
app.include_router(chat_completions.router)


if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="0.0.0.0")
