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
from src.models.enums import RetrievalMode
from src.models.provider import ModelConfiguration
from src.models.retrieval import RetrievalConfiguration
from src.repositories import ApplicationRepository, ProviderRepository, RetrievalConfigurationRepository
from src.repositories.knowledge_base import KnowledgeBaseRepository
from src.schemas.api.domain import (
    ApplicationAskRequest,
    ApplicationAskResponse,
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
    RetrievedSource,
)
from src.services.slug import slugify

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


@router.get("", response_model=List[ApplicationResponse])
def list_applications(session: SessionDep, limit: int = 100, offset: int = 0):
    repo = ApplicationRepository(session)
    return repo.get_all(limit=limit, offset=offset)


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
    return application


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(application_id: UUID, session: SessionDep):
    repo = ApplicationRepository(session)
    application = repo.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


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
    return application


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

    retrieval_cfg = application.retrieval_configuration
    model_cfg = application.model_configuration
    mode = retrieval_cfg.mode if retrieval_cfg else RetrievalMode.HYBRID.value
    top_k = payload.top_k or (retrieval_cfg.top_k if retrieval_cfg else 8)
    model = model_cfg.llm_model if model_cfg else "llama3.2:1b"
    min_score = (retrieval_cfg.score_threshold if retrieval_cfg else None) or 0.0

    # Retrieval (respecting configured mode/top_k). The current OpenSearch
    # client indexes all knowledge bases in one hybrid index; per-KB isolation
    # is a documented limitation (see engineering report).
    if mode == RetrievalMode.BM25.value:
        results = opensearch.search_unified(
            query=payload.query, size=top_k, use_hybrid=False, min_score=min_score
        )
    elif mode == RetrievalMode.VECTOR.value:
        query_vector = await embeddings.embed_query(payload.query)
        results = opensearch.search_chunks_vector(query_embedding=query_vector, size=top_k)
    else:  # hybrid
        query_vector = await embeddings.embed_query(payload.query)
        results = opensearch.search_unified(
            query=payload.query,
            query_embedding=query_vector,
            size=top_k,
            use_hybrid=True,
            min_score=min_score,
        )
    hits = results.get("hits", []) if isinstance(results, dict) else []

    chunks = [
        {
            "chunk_text": h.get("chunk_text", ""),
            "title": h.get("title", ""),
            "score": h.get("score", 0.0),
            "document_id": h.get("document_id"),
            "section_title": h.get("section_title"),
        }
        for h in hits
    ]

    generation = await llm.generate_rag_answer(query=payload.query, chunks=chunks, model=model)
    answer = generation.get("answer", "") if isinstance(generation, dict) else str(generation)

    sources = [
        RetrievedSource(
            document_id=str(h.get("document_id")) if h.get("document_id") else None,
            document_title=h.get("title"),
            chunk_text=h.get("chunk_text", ""),
            score=float(h.get("score", 0.0)),
            section_title=h.get("section_title"),
            retrieval_method=mode,
        )
        for h in hits
    ]

    return ApplicationAskResponse(
        query=payload.query,
        answer=answer,
        sources=sources if application.citations_enabled else [],
        search_mode=mode,
        chunks_used=len(chunks),
    )
