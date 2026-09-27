"""Knowledge Base management endpoints (Admin Control Plane)."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from src.auth import Principal, Role
from src.config import split_csv
from src.dependencies import PrincipalDep, SessionDep, SettingsDep
from src.models.knowledge_base import KnowledgeBase
from src.repositories import DocumentRepository, GrantRepository, KnowledgeBaseRepository
from src.schemas.api.domain import (
    KnowledgeBaseAccessCheck,
    KnowledgeBaseCreate,
    KnowledgeBaseGrantResponse,
    KnowledgeBaseGrantsUpdate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from src.services.access import resolve_scope
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


# Registered before /{kb_id} so the literal path is not captured as an ID.
@router.get("/access-check", response_model=KnowledgeBaseAccessCheck)
def check_access(
    session: SessionDep,
    settings: SettingsDep,
    subject: str = Query(..., min_length=1, description="The caller's subject claim"),
    groups: Optional[str] = Query(None, description="Comma-separated groups claim values"),
    roles: Optional[str] = Query(None, description="Comma-separated raw roles claim values"),
):
    """Preview which knowledge bases a (hypothetical) querier could retrieve from.

    Evaluates the same resolver the answer paths use, so admins can verify a
    grant set against an IdP identity before anyone relies on it.
    """
    principal = Principal(
        subject=subject,
        roles=frozenset({Role.QUERIER}),
        groups=frozenset(split_csv(groups or "")),
        raw_roles=frozenset(split_csv(roles or "")),
        auth_method="jwt",
    )
    scope = resolve_scope(principal, session, settings)
    kbs = KnowledgeBaseRepository(session).get_all(limit=10_000)
    visible = kbs if scope.is_unrestricted else [kb for kb in kbs if str(kb.id) in scope.kb_ids]
    return KnowledgeBaseAccessCheck(
        unrestricted=scope.is_unrestricted,
        reason=scope.reason,
        knowledge_bases=[{"id": kb.id, "name": kb.name, "slug": kb.slug} for kb in visible],
    )


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


@router.get("/{kb_id}/grants", response_model=List[KnowledgeBaseGrantResponse])
def list_grants(kb_id: UUID, session: SessionDep):
    """Who may retrieve from this knowledge base when authentication is on."""
    if not KnowledgeBaseRepository(session).get_by_id(kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return GrantRepository(session).list_for_knowledge_base(kb_id)


@router.put("/{kb_id}/grants", response_model=List[KnowledgeBaseGrantResponse])
def replace_grants(kb_id: UUID, payload: KnowledgeBaseGrantsUpdate, session: SessionDep, principal: PrincipalDep):
    """Replace the knowledge base's grants with exactly the submitted set."""
    if not KnowledgeBaseRepository(session).get_by_id(kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    entries = [(g.principal_type, g.principal_value) for g in payload.grants]
    return GrantRepository(session).replace_for_knowledge_base(kb_id, entries, created_by=principal.subject)
