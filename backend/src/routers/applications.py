"""RAG Application management endpoints + test/ask playground."""

from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import (
    EmbeddingsDep,
    LLMProviderDep,
    OpenSearchDep,
    SessionDep,
)
from src.models.application import RAGApplication
from src.models.provider import ModelConfiguration
from src.models.retrieval import RetrievalConfiguration
from src.repositories import (
    ApiKeyRepository,
    ApplicationRepository,
    ProviderRepository,
    RetrievalConfigurationRepository,
)
from src.repositories.knowledge_base import KnowledgeBaseRepository
from src.routers.knowledge_bases import refresh_counts
from src.schemas.api.domain import (
    ApplicationAskRequest,
    ApplicationAskResponse,
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
    RetrievedSource,
)
from src.services.applications import answer_for_application
from src.services.slug import slugify

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _with_fresh_kb_counts(application, session):
    """Refresh the nested knowledge-base counters before serialisation.

    The admin UI's readiness strip keys "Knowledge indexed" off these, so
    serving the raw (never-decremented) columns reports an assistant as ready
    when its knowledge base has been emptied.
    """
    for kb in application.knowledge_bases:
        refresh_counts(kb, session)
    return application


@router.get("", response_model=List[ApplicationResponse])
def list_applications(session: SessionDep, limit: int = 100, offset: int = 0):
    repo = ApplicationRepository(session)
    return [_with_fresh_kb_counts(a, session) for a in repo.get_all(limit=limit, offset=offset)]


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, session: SessionDep):
    repo = ApplicationRepository(session)
    slug = slugify(payload.name)
    if repo.get_by_slug(slug):
        raise HTTPException(status_code=409, detail=f"Application '{slug}' already exists")

    provider_repo = ProviderRepository(session)
    model_config = provider_repo.create_configuration(
        ModelConfiguration(
            name=f"{slug}-model-config",
            llm_provider=payload.llm_provider,
            llm_endpoint=payload.llm_endpoint,
            llm_model=payload.llm_model,
            embedding_provider=payload.embedding_provider,
            embedding_model=payload.embedding_model,
            vlm_enabled=payload.vlm_enabled,
            vlm_provider=payload.vlm_provider,
            vlm_model=payload.vlm_model,
            generation_params={
                "temperature": payload.temperature,
                "max_tokens": payload.max_tokens,
            },
        )
    )

    retrieval_repo = RetrievalConfigurationRepository(session)
    retrieval_config = retrieval_repo.create(
        RetrievalConfiguration(
            name=f"{slug}-retrieval-config",
            mode=payload.retrieval_mode.value,
            top_k=payload.top_k,
            score_threshold=payload.score_threshold,
        )
    )

    application = RAGApplication(
        name=payload.name,
        slug=slug,
        description=payload.description,
        system_prompt=payload.system_prompt,
        rag_strategy=payload.rag_strategy.value,
        model_configuration_id=model_config.id,
        retrieval_configuration_id=retrieval_config.id,
        streaming_enabled=payload.streaming_enabled,
        citations_enabled=payload.citations_enabled,
    )
    application = repo.create(application)
    if payload.knowledge_base_ids:
        repo.set_knowledge_bases(application, payload.knowledge_base_ids)

    # Provision a default key up front so the caller leaves creation with a
    # usable endpoint. The plaintext is attached transiently and returned once.
    _, raw_key = ApiKeyRepository(session).create(application.id, name="Default key")
    application.api_key = raw_key
    return _with_fresh_kb_counts(application, session)


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(application_id: UUID, session: SessionDep):
    repo = ApplicationRepository(session)
    application = repo.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return _with_fresh_kb_counts(application, session)


@router.patch("/{application_id}", response_model=ApplicationResponse)
def update_application(application_id: UUID, payload: ApplicationUpdate, session: SessionDep):
    repo = ApplicationRepository(session)
    application = repo.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    data = payload.model_dump(exclude_unset=True)
    kb_ids = data.pop("knowledge_base_ids", None)
    if "rag_strategy" in data and data["rag_strategy"] is not None:
        data["rag_strategy"] = data["rag_strategy"].value
    application = repo.update(application, data)
    if kb_ids is not None:
        repo.set_knowledge_bases(application, kb_ids)
    return _with_fresh_kb_counts(application, session)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(application_id: UUID, session: SessionDep):
    repo = ApplicationRepository(session)
    application = repo.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    repo.delete(application)
    return None


@router.post("/{application_id}/ask", response_model=ApplicationAskResponse)
async def ask_application(
    application_id: UUID,
    payload: ApplicationAskRequest,
    session: SessionDep,
    opensearch: OpenSearchDep,
    embeddings: EmbeddingsDep,
    llm: LLMProviderDep,
):
    """Answer a question using the application's stored configuration.

    The application's retrieval mode, top-K and model are honoured rather than
    ignored: configuration drives the request end-to-end.
    """
    repo = ApplicationRepository(session)
    application = repo.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    result = await answer_for_application(
        application=application,
        query=payload.query,
        opensearch=opensearch,
        embeddings=embeddings,
        llm=llm,
        top_k_override=payload.top_k,
    )

    sources = [
        RetrievedSource(
            document_id=str(h.get("document_id")) if h.get("document_id") else None,
            document_title=h.get("title"),
            chunk_text=h.get("chunk_text", ""),
            score=float(h.get("score", 0.0)),
            section_title=h.get("section_title"),
            retrieval_method=result.mode,
        )
        for h in result.hits
    ]

    return ApplicationAskResponse(
        query=payload.query,
        answer=result.answer,
        sources=sources if application.citations_enabled else [],
        search_mode=result.mode,
        chunks_used=len(result.chunks),
    )
