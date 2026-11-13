"""Document management + ingestion endpoints."""
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from src.auth import Principal, Role
from src.config import split_csv
from src.dependencies import (
    CacheDep,
    DatabaseDep,
    IngestionServiceDep,
    OpenSearchDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    require_ingestion_ready,
)
from src.models.access import GrantPrincipalType, principal_key
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.repositories import (
    DocumentRepository,
    IngestionJobRepository,
    KnowledgeBaseRepository,
    RestrictionRepository,
)
from src.schemas.api.domain import (
    DocumentAccessCheck,
    DocumentResponse,
    DocumentRestrictionEntry,
    DocumentRestrictionsResponse,
    DocumentRestrictionsUpdate,
    DocumentRestrictionWarning,
    IngestionJobResponse,
)
from src.services.access import resolve_scope
from src.services.ingestion.uploads import TEXT_CONTENT_TYPES, decode_text, display_name, read_capped, stored_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["documents"])

async def _run_ingestion(
    database,
    ingestion_service,
    document_id: UUID,
    job_id: UUID,
    knowledge_base_id: UUID,
) -> None:
    """Background task: run the ingestion pipeline in its own DB session."""
    with database.get_session() as session:
        doc_repo = DocumentRepository(session)
        job_repo = IngestionJobRepository(session)
        kb_repo = KnowledgeBaseRepository(session)
        document = doc_repo.get_by_id(document_id)
        job = job_repo.get_by_id(job_id)
        kb = kb_repo.get_by_id(knowledge_base_id)
        if not document or not job or not kb:
            return
        await ingestion_service.ingest_document(
            document=document,
            job=job,
            document_repo=doc_repo,
            ingestion_repo=job_repo,
            knowledge_base=kb,
        )
        # Refresh knowledge-base counters.
        kb.document_count = doc_repo.count(kb.id)
        kb.chunk_count = doc_repo.count_chunks(kb.id)
        kb_repo.update(kb, {})


@router.get("/knowledge-bases/{kb_id}/documents", response_model=List[DocumentResponse])
def list_documents(kb_id: UUID, session: SessionDep, limit: int = 100, offset: int = 0):
    kb_repo = KnowledgeBaseRepository(session)
    if not kb_repo.get_by_id(kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    doc_repo = DocumentRepository(session)
    return doc_repo.list_for_knowledge_base(kb_id, limit=limit, offset=offset)


@router.get("/documents", response_model=List[DocumentResponse])
def list_all_documents(session: SessionDep, limit: int = 100, offset: int = 0):
    return DocumentRepository(session).list_all(limit=limit, offset=offset)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: UUID, session: SessionDep):
    doc = DocumentRepository(session).get_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ingestion_ready)],
)
async def upload_document(
    kb_id: UUID,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
):
    """Upload a document, persist it, and queue ingestion asynchronously."""
    kb_repo = KnowledgeBaseRepository(session)
    kb = kb_repo.get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    raw = await read_capped(file, settings.ingestion.max_upload_mb)
    checksum = hashlib.sha256(raw).hexdigest()

    doc_repo = DocumentRepository(session)
    existing = doc_repo.get_by_checksum(kb_id, checksum)
    if existing:
        raise HTTPException(status_code=409, detail="Identical document already exists in this knowledge base")

    content_type = file.content_type or "application/octet-stream"
    name = display_name(file.filename)
    raw_text = None
    file_path = None
    if content_type in TEXT_CONTENT_TYPES or name.lower().endswith((".txt", ".md")):
        raw_text = decode_text(raw)
    else:
        # Persist binary uploads (e.g. PDFs) for parsing, under a name nothing in the upload can influence.
        path = stored_path(settings, kb_id, checksum, file.filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        file_path = str(path)

    document = doc_repo.create(
        Document(
            knowledge_base_id=kb_id,
            title=title or (name if file.filename else "Untitled document"),
            source_uri=name if file.filename else None,
            content_type=content_type,
            file_path=file_path,
            file_size_bytes=len(raw),
            checksum=checksum,
            raw_text=raw_text,
            status=IngestionStatus.QUEUED.value,
        )
    )
    job = IngestionJobRepository(session).create(
        IngestionJob(
            document_id=document.id,
            knowledge_base_id=kb_id,
            status=IngestionStatus.QUEUED.value,
        )
    )

    background_tasks.add_task(
        _run_ingestion, database, ingestion_service, document.id, job.id, kb_id
    )
    return document


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ingestion_ready)],
)
async def reprocess_document(
    document_id: UUID,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    opensearch: OpenSearchDep,
    background_tasks: BackgroundTasks,
):
    """Re-run the ingestion pipeline for an existing document."""
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not document.raw_text and not (document.file_path and Path(document.file_path).exists()):
        raise HTTPException(
            status_code=409,
            detail="The original file is gone (removed by upload cleanup), so this document cannot be reprocessed. Add it to the knowledge base again.",
        )

    # Clear previous chunks (in the index too, or the document would be answered from twice) + reset status.
    _purge_index_chunks(opensearch, document.id)
    doc_repo.delete_chunks_for_document(document.id)
    document.status = IngestionStatus.QUEUED.value
    document.chunk_count = 0
    document.error = None
    doc_repo.save(document)

    job = IngestionJobRepository(session).create(
        IngestionJob(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            status=IngestionStatus.QUEUED.value,
        )
    )
    background_tasks.add_task(
        _run_ingestion, database, ingestion_service, document.id, job.id, document.knowledge_base_id
    )
    return job


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, session: SessionDep, opensearch: OpenSearchDep):
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    # The index first: a deleted document must stop being answerable, so if its chunks cannot be removed it stays.
    _purge_index_chunks(opensearch, document.id)
    doc_repo.delete(document)
    return None


def _purge_index_chunks(opensearch, document_id: UUID) -> None:
    try:
        opensearch.purge_document_chunks(str(document_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not remove the chunks of document %s from the index: %s", document_id, exc)
        raise HTTPException(
            status_code=502, detail="The search index could not be updated, so nothing was changed. Try again."
        ) from exc


def _restrictions_response(
    document_id: UUID, rows, session, settings, chunks_updated: Optional[int] = None
) -> DocumentRestrictionsResponse:
    """The restrictions, with a warning for each value that matches no claim value the platform has seen."""
    known = RestrictionRepository(session).known_values(settings.auth)
    warnings = [
        DocumentRestrictionWarning(
            principal_type=row.principal_type,
            principal_value=row.principal_value,
            message=(
                f"No known {row.principal_type} has the value '{row.principal_value}' (not in any grant, role mapping "
                "or recent caller). If it is a typo, nobody will be able to read this document."
            ),
        )
        for row in rows
        if row.principal_type != GrantPrincipalType.EVERYONE.value
        and row.principal_value not in known.get(row.principal_type, set())
    ]
    return DocumentRestrictionsResponse(
        document_id=document_id,
        restrictions=[DocumentRestrictionEntry.model_validate(row, from_attributes=True) for row in rows],
        warnings=warnings,
        chunks_updated=chunks_updated,
    )


@router.get("/knowledge-bases/{kb_id}/document-restrictions", response_model=Dict[str, List[DocumentRestrictionEntry]])
def list_document_restrictions(kb_id: UUID, session: SessionDep):
    """Restrictions of every restricted Document in a knowledge base, by document ID."""
    if not KnowledgeBaseRepository(session).get_by_id(kb_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    grouped = RestrictionRepository(session).list_for_knowledge_base(kb_id)
    return {
        document_id: [DocumentRestrictionEntry.model_validate(row, from_attributes=True) for row in rows]
        for document_id, rows in grouped.items()
    }


@router.get("/documents/{document_id}/restrictions", response_model=DocumentRestrictionsResponse)
def get_restrictions(document_id: UUID, session: SessionDep, settings: SettingsDep):
    """Who a Document is restricted to; none means it inherits its knowledge base's grants."""
    if not DocumentRepository(session).get_by_id(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    rows = RestrictionRepository(session).list_for_document(document_id)
    return _restrictions_response(document_id, rows, session, settings)


@router.put("/documents/{document_id}/restrictions", response_model=DocumentRestrictionsResponse)
def replace_restrictions(
    document_id: UUID,
    payload: DocumentRestrictionsUpdate,
    session: SessionDep,
    settings: SettingsDep,
    opensearch: OpenSearchDep,
    cache: CacheDep,
    principal: PrincipalDep,
):
    """Restrict a Document to exactly the submitted Principals (none clears the restriction).

    Restrictions only narrow: a caller still needs a Grant on the knowledge base. Chunks already indexed are updated in
    place, without re-ingesting.
    """
    document = DocumentRepository(session).get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    entries = sorted({(e.principal_type, e.principal_value) for e in payload.restrictions})
    keys = [principal_key(t, v) for t, v in entries]

    # The index first: if it cannot be updated nothing changes, and a restriction is never reported that is not in force.
    try:
        updated = opensearch.set_document_restrictions(str(document.id), keys)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not update the chunks of document %s: %s", document.id, exc)
        raise HTTPException(
            status_code=502, detail="The search index could not be updated, so the restriction was not changed. Try again."
        ) from exc
    rows = RestrictionRepository(session).replace_for_document(document.id, entries, created_by=principal.subject)
    if cache:
        # Cached answers were built under the old restriction.
        cache.clear()
    return _restrictions_response(document.id, rows, session, settings, chunks_updated=updated)


@router.delete("/documents/{document_id}/restrictions", status_code=status.HTTP_204_NO_CONTENT)
def clear_restrictions(
    document_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    opensearch: OpenSearchDep,
    cache: CacheDep,
    principal: PrincipalDep,
):
    """Remove a Document's restrictions, so it inherits its knowledge base's grants again."""
    replace_restrictions(document_id, DocumentRestrictionsUpdate(), session, settings, opensearch, cache, principal)
    return None


@router.get("/documents/{document_id}/access-check", response_model=DocumentAccessCheck)
def check_document_access(
    document_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    subject: str = Query(..., min_length=1, description="The caller's subject claim"),
    groups: Optional[str] = Query(None, description="Comma-separated groups claim values"),
    roles: Optional[str] = Query(None, description="Comma-separated raw roles claim values"),
):
    """Whether a (hypothetical) querier could read this Document, by the resolver the answer paths use."""
    document = DocumentRepository(session).get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    caller = Principal(
        subject=subject,
        roles=frozenset({Role.QUERIER}),
        groups=frozenset(split_csv(groups or "")),
        raw_roles=frozenset(split_csv(roles or "")),
        auth_method="jwt",
    )
    scope = resolve_scope(caller, session, settings)
    restriction_keys = RestrictionRepository(session).keys_for_document(document.id)
    kb_id = str(document.knowledge_base_id)
    granted = bool(scope.restrict([kb_id]).permitted)
    restricted = bool(restriction_keys)
    applies = restricted and scope.principal_keys is not None
    matched = bool(set(restriction_keys) & scope.principal_keys) if applies else None
    can_read = scope.permits_document(kb_id, restriction_keys)
    if not granted:
        reason = "The caller holds no grant on this document's knowledge base."
    elif applies and not matched:
        reason = "The caller has a grant on the knowledge base, but matches none of the document's restrictions."
    elif applies:
        reason = "The caller has a grant on the knowledge base and matches a restriction."
    elif restricted:
        reason = f"Restrictions are not applied to this caller ({scope.reason})."
    else:
        reason = "The document is unrestricted, so it inherits the knowledge base's grants."
    return DocumentAccessCheck(
        document_id=document.id,
        can_read=can_read,
        reason=reason,
        knowledge_base_granted=granted,
        restricted=restricted,
        restriction_matched=matched,
    )


@router.get("/ingestion-jobs", response_model=List[IngestionJobResponse])
def list_ingestion_jobs(session: SessionDep, limit: int = 100, offset: int = 0):
    return IngestionJobRepository(session).get_all(limit=limit, offset=offset)


@router.get("/documents/{document_id}/ingestion-job", response_model=IngestionJobResponse)
def get_document_ingestion_job(document_id: UUID, session: SessionDep):
    job = IngestionJobRepository(session).get_for_document(document_id)
    if not job:
        raise HTTPException(status_code=404, detail="No ingestion job found for document")
    return job
