from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Callable, ContextManager, Generator, Optional

if TYPE_CHECKING:
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.orm import Session
    from src.services.ingestion.service import IngestionService
else:
    try:
        from fastapi import Depends, HTTPException, Request
        from sqlalchemy.orm import Session
    except ImportError:
        pass

from src.auth import Principal, get_principal
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.services.access import RetrievalScope, resolve_scope
from src.services.agents.agentic_rag import AgenticRAGService
from src.services.agents.factory import make_agentic_rag_service
from src.services.audit import AuditRecorder, NoopAuditRecorder
from src.services.cache.client import CacheClient
from src.services.chat.limits import ChatLimiter
from src.services.ingestion.url_fetch import UrlFetcher
from src.services.langfuse.client import LangfuseTracer
from src.services.ollama.client import OllamaClient
from src.services.opensearch.client import OpenSearchClient
from src.services.pdf_parser.parser import PDFParserService
from src.services.providers.embeddings.base import EmbeddingProvider
from src.services.providers.llm.base import LLMProvider
from src.services.providers.llm.factory import make_provider_registry
from src.services.providers.llm.registry import ProviderRegistry
from src.services.providers.vlm.base import VLMProvider
from src.services.rerank.client import Reranker
from src.services.telemetry import NOOP_TELEMETRY, Telemetry


@lru_cache
def get_settings() -> Settings:
    """Get application settings."""
    return Settings()


def get_request_settings(request: Request) -> Settings:
    """Get settings from the request state."""
    return request.app.state.settings


def get_database(request: Request) -> BaseDatabase:
    """Get database from the request state."""
    return request.app.state.database


def get_db_session(database: Annotated[BaseDatabase, Depends(get_database)]) -> Generator[Session, None, None]:
    """Get database session dependency."""
    with database.get_session() as session:
        yield session


def get_session_opener(request: Request) -> Callable[[], ContextManager[Session]]:
    """Open short-lived sessions of one's own, for work that outlives the
    handler (a streamed response body runs after the request session closes).
    Honours a ``get_db_session`` override, as tests use."""
    override = request.app.dependency_overrides.get(get_db_session)
    if override is None:
        return get_database(request).get_session

    @contextmanager
    def open_overridden():
        sessions = override()
        try:
            yield next(sessions)
        finally:
            sessions.close()

    return open_overridden


def get_opensearch_client(request: Request) -> OpenSearchClient:
    """Get OpenSearch client from the request state."""
    return request.app.state.opensearch_client


def get_pdf_parser(request: Request) -> PDFParserService:
    """Get PDF parser service from the request state."""
    return request.app.state.pdf_parser


def get_embeddings_service(request: Request) -> EmbeddingProvider:
    """Get embedding provider from the request state."""
    return request.app.state.embeddings_service


def get_url_fetcher(request: Request) -> Optional[UrlFetcher]:
    """The fetcher for URL Documents (it refuses every host unless the operator listed it)."""
    return getattr(request.app.state, "url_fetcher", None)


def get_vlm_provider(request: Request) -> Optional[VLMProvider]:
    """Get the optional VLM provider from the request state."""
    return getattr(request.app.state, "vlm_provider", None)


def get_ollama_client(request: Request) -> OllamaClient:
    """Get Ollama client from the request state."""
    return request.app.state.ollama_client


def get_langfuse_tracer(request: Request) -> LangfuseTracer:
    """Get Langfuse tracer from the request state."""
    return request.app.state.langfuse_tracer


def get_llm_provider(request: Request) -> LLMProvider:
    """Get the configured LLM provider from the request state."""
    return request.app.state.llm_provider


def get_provider_registry(request: Request) -> ProviderRegistry:
    """The registry routing Applications to their own inference endpoints.

    Apps assembled without the lifespan get a throwaway registry, which only
    loses caching; Applications without an endpoint never consult it.
    """
    registry = getattr(request.app.state, "provider_registry", None)
    return registry if registry is not None else make_provider_registry(get_settings())


def get_reranker(request: Request) -> Optional[Reranker]:
    """The optional reranker (None when ``RERANK__ENABLED`` is off or the app has none)."""
    return getattr(request.app.state, "reranker", None)


def get_cache_client(request: Request) -> CacheClient | None:
    """Get cache client from the request state."""
    return getattr(request.app.state, "cache_client", None)


def get_chat_limiter(request: Request) -> Optional[ChatLimiter]:
    """The per-user chat limiter (None for an app assembled without the lifespan: no limits)."""
    return getattr(request.app.state, "chat_limiter", None)


def require_ingestion_ready(request: Request) -> None:
    """Refuse ingestion (409) while the chunk index disagrees with configuration."""
    guard = getattr(request.app.state, "ingestion_guard", None)
    if guard:
        raise HTTPException(status_code=409, detail=guard)


def get_audit_recorder(request: Request) -> AuditRecorder:
    """Get the audit recorder; apps assembled without the lifespan record nothing."""
    return getattr(request.app.state, "audit_recorder", None) or NoopAuditRecorder()


def get_telemetry(request: Request) -> Telemetry:
    """The telemetry of the app; apps assembled without the lifespan emit nothing."""
    return getattr(request.app.state, "telemetry", None) or NOOP_TELEMETRY


# Dependency annotations
SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[BaseDatabase, Depends(get_database)]
SessionDep = Annotated[Session, Depends(get_db_session)]
OpenSearchDep = Annotated[OpenSearchClient, Depends(get_opensearch_client)]
PDFParserDep = Annotated[PDFParserService, Depends(get_pdf_parser)]
EmbeddingsDep = Annotated[EmbeddingProvider, Depends(get_embeddings_service)]
VLMDep = Annotated[Optional[VLMProvider], Depends(get_vlm_provider)]
UrlFetcherDep = Annotated[Optional[UrlFetcher], Depends(get_url_fetcher)]
OllamaDep = Annotated[OllamaClient, Depends(get_ollama_client)]
LangfuseDep = Annotated[LangfuseTracer, Depends(get_langfuse_tracer)]
CacheDep = Annotated[CacheClient | None, Depends(get_cache_client)]
LLMProviderDep = Annotated[LLMProvider, Depends(get_llm_provider)]
RerankerDep = Annotated[Optional[Reranker], Depends(get_reranker)]
ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]
ChatLimiterDep = Annotated[Optional[ChatLimiter], Depends(get_chat_limiter)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
AuditDep = Annotated[AuditRecorder, Depends(get_audit_recorder)]
TelemetryDep = Annotated[Telemetry, Depends(get_telemetry)]
SessionOpenerDep = Annotated[Callable[[], ContextManager[Session]], Depends(get_session_opener)]


def get_ingestion_service(
    opensearch: OpenSearchDep,
    embeddings: EmbeddingsDep,
    settings: SettingsDep,
    pdf_parser: PDFParserDep,
    vlm: VLMDep,
    url_fetcher: UrlFetcherDep,
) -> "IngestionService":
    """Construct the generic ingestion service from platform collaborators."""
    from src.services.ingestion.factory import make_ingestion_service

    return make_ingestion_service(opensearch, embeddings, pdf_parser, settings, vlm, url_fetcher)


IngestionServiceDep = Annotated["IngestionService", Depends(get_ingestion_service)]


def get_retrieval_scope(request: Request, principal: PrincipalDep, settings: SettingsDep) -> RetrievalScope:
    """Knowledge bases the caller may retrieve from (resolved once per request).

    With authentication disabled the scope is unrestricted and needs no
    database, so none is opened: endpoints stay usable exactly as before, even
    in apps assembled without a database. Otherwise a short-lived session reads
    the grants (honouring a ``get_db_session`` override, as tests use).
    """
    if settings.auth.mode == "disabled":
        return RetrievalScope.unrestricted("authentication disabled")

    override = request.app.dependency_overrides.get(get_db_session)
    if override is not None:
        sessions = override()
        try:
            return resolve_scope(principal, next(sessions), settings)
        finally:
            sessions.close()

    with get_database(request).get_session() as session:
        return resolve_scope(principal, session, settings)


ScopeDep = Annotated[RetrievalScope, Depends(get_retrieval_scope)]


def get_agentic_rag_service(
    opensearch: OpenSearchDep,
    ollama: OllamaDep,
    embeddings: EmbeddingsDep,
    langfuse: LangfuseDep,
    settings: Annotated[Settings, Depends(get_settings)],
    scope: Annotated[Optional[RetrievalScope], Depends(get_retrieval_scope)] = None,
    llm: Annotated[Optional[LLMProvider], Depends(get_llm_provider)] = None,
    reranker: Annotated[Optional[Reranker], Depends(get_reranker)] = None,
) -> AgenticRAGService:
    """Get agentic RAG service, with its retriever limited to the caller's scope.

    Every node's chat model comes from ``llm`` (the configured ``LLM__PROVIDER``).
    ``scope`` and ``llm`` default to None only for direct calls; under FastAPI
    they are always resolved. Without ``llm`` the nodes use Ollama.
    """
    decision = scope.restrict(None) if scope is not None else None
    return make_agentic_rag_service(
        opensearch_client=opensearch,
        ollama_client=ollama,
        embeddings_client=embeddings,
        langfuse_tracer=langfuse,
        model=settings.default_llm_model,
        knowledge_base_ids=decision.permitted if decision is not None else None,
        principal_keys=None if decision is None or decision.principal_keys is None else sorted(decision.principal_keys),
        llm_provider=llm,
        reranker=reranker,
    )


AgenticRAGDep = Annotated[AgenticRAGService, Depends(get_agentic_rag_service)]
