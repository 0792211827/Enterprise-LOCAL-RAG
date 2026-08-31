"""Knowledge Base management endpoints (Admin Control Plane)."""

from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import SessionDep, SettingsDep
from src.models.knowledge_base import KnowledgeBase
from src.repositories import DocumentRepository, KnowledgeBaseRepository
from src.schemas.api.domain import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from src.services.slug import slugify

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge-bases"])


def refresh_counts(kb: KnowledgeBase, session) -> KnowledgeBase:
    """Recompute the denormalised counters from the documents/chunks tables.

    The columns are only written by the ingestion background task and are never
    decremented on delete, so anything serving them must refresh first or it
    reports documents that no longer exist.
    """
    doc_repo = DocumentRepository(session)
    kb.document_count = doc_repo.count(kb.id)
    kb.chunk_count = doc_repo.count_chunks(kb.id)
    return kb


@router.get("", response_model=List[KnowledgeBaseResponse])
def list_knowledge_bases(session: SessionDep, limit: int = 100, offset: int = 0):
    repo = KnowledgeBaseRepository(session)
    return [refresh_counts(kb, session) for kb in repo.get_all(limit=limit, offset=offset)]


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(payload: KnowledgeBaseCreate, session: SessionDep, settings: SettingsDep):
    repo = KnowledgeBaseRepository(session)
    slug = slugify(payload.name)
    if repo.get_by_slug(slug):
        raise HTTPException(status_code=409, detail=f"Knowledge base '{slug}' already exists")

    index_name = f"{settings.opensearch.index_name}-{slug}-{settings.opensearch.chunk_index_suffix}"
    kb = KnowledgeBase(
        name=payload.name,
        slug=slug,
        description=payload.description,
        index_name=index_name,
        embedding_provider=payload.embedding_provider,
        embedding_model=payload.embedding_model,
        embedding_dimension=payload.embedding_dimension,
        retrieval_mode=payload.retrieval_mode.value,
        default_top_k=payload.default_top_k,
    )
    return repo.create(kb)


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(kb_id: UUID, session: SessionDep):
    repo = KnowledgeBaseRepository(session)
    kb = repo.get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return refresh_counts(kb, session)


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
def update_knowledge_base(kb_id: UUID, payload: KnowledgeBaseUpdate, session: SessionDep):
    repo = KnowledgeBaseRepository(session)
    kb = repo.get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    data = payload.model_dump(exclude_unset=True)
    if "retrieval_mode" in data and data["retrieval_mode"] is not None:
        data["retrieval_mode"] = data["retrieval_mode"].value
    return repo.update(kb, data)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(kb_id: UUID, session: SessionDep):
    repo = KnowledgeBaseRepository(session)
    kb = repo.get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    repo.delete(kb)
    return None
