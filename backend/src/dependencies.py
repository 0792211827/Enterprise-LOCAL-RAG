from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Callable, ContextManager, Generator, Optional

if TYPE_CHECKING:
    from fastapi import Depends, Request
    from sqlalchemy.orm import Session
    from src.services.ingestion.service import IngestionService
else:
    try:
        from fastapi import Depends, Request
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
from src.services.langfuse.client import LangfuseTracer
from src.services.ollama.client import OllamaClient
from src.services.opensearch.client import OpenSearchClient
from src.services.pdf_parser.parser import PDFParserService
from src.services.providers.embeddings.base import EmbeddingProvider
from src.services.providers.llm.base import LLMProvider
from src.services.providers.vlm.base import VLMProvider


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


def get_cache_client(request: Request) -> CacheClient | None:
    """Get cache client from the request state."""
    return getattr(request.app.state, "cache_client", None)


def get_audit_recorder(request: Request) -> AuditRecorder:
    """Get the audit recorder; apps assembled without the lifespan record nothing."""
    return getattr(request.app.state, "audit_recorder", None) or NoopAuditRecorder()


# Dependency annotations
SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[BaseDatabase, Depends(get_database)]
SessionDep = Annotated[Session, Depends(get_db_session)]
OpenSearchDep = Annotated[OpenSearchClient, Depends(get_opensearch_client)]
PDFParserDep = Annotated[PDFParserService, Depends(get_pdf_parser)]
EmbeddingsDep = Annotated[EmbeddingProvider, Depends(get_embeddings_service)]
VLMDep = Annotated[Optional[VLMProvider], Depends(get_vlm_provider)]
OllamaDep = Annotated[OllamaClient, Depends(get_ollama_client)]
LangfuseDep = Annotated[LangfuseTracer, Depends(get_langfuse_tracer)]
CacheDep = Annotated[CacheClient | None, Depends(get_cache_client)]
LLMProviderDep = Annotated[LLMProvider, Depends(get_llm_provider)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
AuditDep = Annotated[AuditRecorder, Depends(get_audit_recorder)]
SessionOpenerDep = Annotated[Callable[[], ContextManager[Session]], Depends(get_session_opener)]


def get_ingestion_service(
    opensearch: OpenSearchDep,
    embeddings: EmbeddingsDep,
    settings: SettingsDep,
    pdf_parser: PDFParserDep,
) -> "IngestionService":
    """Construct the generic ingestion service from platform collaborators."""
    from src.services.indexing.text_chunker import TextChunker
    from src.services.ingestion.service import IngestionService

    chunker = TextChunker(
        chunk_size=settings.chunking.chunk_size,
        overlap_size=settings.chunking.overlap_size,
        min_chunk_size=settings.chunking.min_chunk_size,
    )
    return IngestionService(
        chunker=chunker,
        embeddings_provider=embeddings,
        opensearch_client=opensearch,
        pdf_parser=pdf_parser,
    )


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
) -> AgenticRAGService:
    """Get agentic RAG service, with its retriever limited to the caller's scope.

    ``scope`` defaults to None (unscoped) only for direct calls; under FastAPI
    it is always resolved.
    """
    return make_agentic_rag_service(
        opensearch_client=opensearch,
        ollama_client=ollama,
        embeddings_client=embeddings,
        langfuse_tracer=langfuse,
        model=settings.ollama_model,
        knowledge_base_ids=scope.restrict(None).permitted if scope is not None else None,
    )


AgenticRAGDep = Annotated[AgenticRAGService, Depends(get_agentic_rag_service)]
