"""Document management + ingestion endpoints."""
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from src.auth import Principal, Role
from src.config import split_csv
from src.dependencies import (
    AuditDep,
    CacheDep,
    DatabaseDep,
    IngestionServiceDep,
    OpenSearchDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    UrlFetcherDep,
    require_ingestion_ready,
)
from src.models import AuditAction, AuditOutcome
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
    DocumentRefreshResponse,
    DocumentResponse,
    DocumentRestrictionEntry,
    DocumentRestrictionsResponse,
    DocumentRestrictionsUpdate,
    DocumentRestrictionWarning,
    IngestionJobResponse,
    UrlDocumentCreate,
)
from src.services.access import resolve_scope
from src.services.audit import AuditContext, record_event
from src.services.ingestion.lifecycle import IndexNotUpdated, stage_replacement
from src.services.ingestion.lifecycle import delete_document as remove_document
from src.services.ingestion.runner import pending_replacement, run_ingestion
from src.services.ingestion.types import UnsupportedDocument, sniff_document_type, store_file
from src.services.ingestion.uploads import display_name, read_capped
from src.services.ingestion.url_documents import fetch_content, is_url_document, url_source
from src.services.ingestion.url_fetch import UrlFetchError, audit_form

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["documents"])

def _queue(background_tasks, database, ingestion_service, settings, document, job, replacement: bool = False) -> None:
    background_tasks.add_task(
        run_ingestion,
        database,
        ingestion_service,
        document.id,
        job.id,
        document.knowledge_base_id,
        settings.ingestion.concurrency,
        replacement,
    )


def _audit(request: Request, principal, recorder, operation: str, document_id: UUID, knowledge_base_id: UUID, outcome: str = AuditOutcome.OK.value, **details) -> None:
    """Add, replace and delete leave an event of metadata only: ids, file name, type, size and a digest -- never text."""
    record_event(
        recorder,
        AuditContext.capture(request, principal),
        action=AuditAction.DOCUMENT_CHANGE.value,
        outcome=outcome,
        details={"operation": operation, "document_id": str(document_id), "knowledge_base_id": str(knowledge_base_id), **details},
    )


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
    request: Request,
    principal: PrincipalDep,
    audit: AuditDep,
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

    try:
        document_type = sniff_document_type(file.filename, raw, file.content_type)
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    name = display_name(file.filename)
    same_name = doc_repo.get_by_source_name(kb_id, name) if file.filename else None
    if same_name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A document named '{name}' already exists in this knowledge base, so adding another would answer from "
                f"both versions. Use Replace on it to update its content (document {same_name.id})."
            ),
        )
    stored = store_file(settings, kb_id, checksum, document_type, raw)

    document = doc_repo.create(
        Document(
            knowledge_base_id=kb_id,
            title=title or (name if file.filename else "Untitled document"),
            source_uri=name if file.filename else None,
            content_type=stored.content_type,
            file_path=stored.file_path,
            file_size_bytes=len(raw),
            checksum=checksum,
            raw_text=stored.raw_text,
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

    _queue(background_tasks, database, ingestion_service, settings, document, job)
    _audit(request, principal, audit, "add", document.id, kb_id, file_name=name, content_type=stored.content_type, size_bytes=len(raw), checksum=checksum)
    return document


@router.post(
    "/knowledge-bases/{kb_id}/documents/url",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ingestion_ready)],
)
async def add_url_document(
    kb_id: UUID,
    payload: UrlDocumentCreate,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    settings: SettingsDep,
    url_fetcher: UrlFetcherDep,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: PrincipalDep,
    audit: AuditDep,
):
    """Add a web page to a Knowledge Base. The page is fetched by the server (only hosts on the operator's allowlist),
    then ingested like a file; a page that cannot be fetched becomes a failed Document with the reason."""
    kb = KnowledgeBaseRepository(session).get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    url = _checked_url(url_fetcher, payload.url)
    doc_repo = DocumentRepository(session)
    existing = doc_repo.get_by_source_name(kb_id, url)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"This page is already in the knowledge base. Use Refresh on it to fetch it again (document {existing.id}).",
        )
    document = doc_repo.create(
        Document(
            knowledge_base_id=kb_id,
            title=(payload.title or "").strip() or url,
            source_uri=url,
            extra_metadata={"source": url_source(url)},
            status=IngestionStatus.QUEUED.value,
        )
    )
    job = IngestionJobRepository(session).create(
        IngestionJob(document_id=document.id, knowledge_base_id=kb_id, status=IngestionStatus.QUEUED.value)
    )
    _queue(background_tasks, database, ingestion_service, settings, document, job)
    _audit(request, principal, audit, "add", document.id, kb_id, source_kind="url", url=audit_form(url))
    return document


def _checked_url(url_fetcher, url: str) -> str:
    """The normal form of an address the server may fetch, or the reason it may not (400 invalid, 403 not allowed)."""
    if url_fetcher is None:
        raise HTTPException(status_code=403, detail="Adding a URL is turned off (INGESTION__URL_ALLOWED_HOSTS is empty).")
    try:
        return url_fetcher.check(url)
    except UrlFetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/documents/{document_id}/refresh",
    response_model=DocumentRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ingestion_ready)],
)
async def refresh_url_document(
    document_id: UUID,
    response: Response,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    settings: SettingsDep,
    url_fetcher: UrlFetcherDep,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: PrincipalDep,
    audit: AuditDep,
):
    """Fetch a URL Document's page again, and replace its content (the safe swap of Replace) only if the page changed."""
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not is_url_document(document):
        raise HTTPException(status_code=409, detail="Only a document added by URL can be refreshed. Use Replace for a file.")
    if document.status in (IngestionStatus.QUEUED.value, IngestionStatus.PROCESSING.value):
        raise HTTPException(status_code=409, detail="The document is still being ingested. Wait for it to finish, then refresh it.")
    if pending_replacement(document):
        raise HTTPException(status_code=409, detail="A refresh of this document is already being ingested.")
    url = _checked_url(url_fetcher, document.source_url)
    ids, shown = (document.id, document.knowledge_base_id), audit_form(url)
    try:
        content = await fetch_content(url_fetcher, url)
    except UrlFetchError as exc:
        _audit(request, principal, audit, "refresh", *ids, outcome=AuditOutcome.ERROR.value, source_kind="url", url=shown)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    try:
        session.refresh(document)  # nothing awaited between this check and the replacement being recorded
    except Exception as exc:  # noqa: BLE001 - deleted while the page was being fetched
        raise HTTPException(status_code=404, detail="Document not found") from exc
    if document.status in (IngestionStatus.QUEUED.value, IngestionStatus.PROCESSING.value) or pending_replacement(document):
        raise HTTPException(status_code=409, detail="The document is being ingested. Wait for it to finish, then refresh it.")
    if content.checksum == document.checksum:
        response.status_code = status.HTTP_200_OK
        _audit(request, principal, audit, "refresh", *ids, source_kind="url", url=shown, changed=False)
        return DocumentRefreshResponse(changed=False, document=DocumentResponse.model_validate(document))
    replacement = stage_replacement(settings, doc_repo, document, shown, content.page.raw, content.document_type)
    job = IngestionJobRepository(session).create(
        IngestionJob(document_id=document.id, knowledge_base_id=document.knowledge_base_id, status=IngestionStatus.QUEUED.value)
    )
    _queue(background_tasks, database, ingestion_service, settings, document, job, replacement=True)
    _audit(
        request, principal, audit, "refresh", *ids, source_kind="url", url=shown, changed=True,
        content_type=replacement["content_type"], size_bytes=len(content.page.raw), checksum=content.checksum,
    )
    return DocumentRefreshResponse(changed=True, document=DocumentResponse.model_validate(document))


@router.post(
    "/documents/{document_id}/replace",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ingestion_ready)],
)
async def replace_document_content(
    document_id: UUID,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: PrincipalDep,
    audit: AuditDep,
    file: UploadFile = File(...),
):
    """Replace a Document's content with a new file, keeping its id, title, Knowledge Base and restrictions.

    The new content is ingested as a new revision while the old one keeps answering; it takes over only when it is
    ready. If it fails, the old content stays and the error is shown on the Document.
    """
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    raw = await read_capped(file, settings.ingestion.max_upload_mb)
    # Checked after the upload is read, with nothing awaited before the replacement is recorded: two requests cannot both pass.
    session.refresh(document)
    if document.status in (IngestionStatus.QUEUED.value, IngestionStatus.PROCESSING.value):
        raise HTTPException(status_code=409, detail="The document is still being ingested. Wait for it to finish, then replace it.")
    if pending_replacement(document):
        raise HTTPException(status_code=409, detail="A replacement of this document is already being ingested.")
    if is_url_document(document):
        raise HTTPException(status_code=409, detail="This document is a web page. Use Refresh to fetch it again.")
    checksum = hashlib.sha256(raw).hexdigest()
    if document.checksum == checksum or doc_repo.get_by_checksum(document.knowledge_base_id, checksum):
        raise HTTPException(status_code=409, detail="Identical document already exists in this knowledge base")
    try:
        document_type = sniff_document_type(file.filename, raw, file.content_type)
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    name = display_name(file.filename)
    replacement = stage_replacement(settings, doc_repo, document, name, raw, document_type)

    job = IngestionJobRepository(session).create(
        IngestionJob(document_id=document.id, knowledge_base_id=document.knowledge_base_id, status=IngestionStatus.QUEUED.value)
    )
    _queue(background_tasks, database, ingestion_service, settings, document, job, replacement=True)
    _audit(request, principal, audit, "replace", document.id, document.knowledge_base_id, file_name=name, content_type=replacement["content_type"], size_bytes=len(raw), checksum=checksum)
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
    settings: SettingsDep,
    opensearch: OpenSearchDep,
    background_tasks: BackgroundTasks,
):
    """Retry a Document: re-run ingestion of its content, or of its failed replacement (the old content keeps answering)."""
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if pending_replacement(document):
        raise HTTPException(status_code=409, detail="A replacement of this document is being ingested. Wait for it to finish.")
    failed = (document.extra_metadata or {}).get("replacement")
    if failed and failed.get("state") == "failed":
        path = failed.get("file_path")
        if not failed.get("raw_text") and not (path and Path(path).exists()):
            raise HTTPException(
                status_code=409,
                detail="The file of the failed replacement is gone, so it cannot be retried. Replace the document again.",
            )
        document.extra_metadata = {**document.extra_metadata, "replacement": {**failed, "state": "queued", "error": None}}
        if document.status == IngestionStatus.COMPLETED.value:
            document.error = None
        doc_repo.save(document)
        replacing = True
    else:
        fetchable = is_url_document(document) and not document.file_path  # its fetch failed: retrying fetches it again
        if not fetchable and not document.raw_text and not (document.file_path and Path(document.file_path).exists()):
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
        replacing = False

    job = IngestionJobRepository(session).create(
        IngestionJob(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            status=IngestionStatus.QUEUED.value,
        )
    )
    _queue(background_tasks, database, ingestion_service, settings, document, job, replacement=replacing)
    return job


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    session: SessionDep,
    opensearch: OpenSearchDep,
    request: Request,
    principal: PrincipalDep,
    audit: AuditDep,
):
    """Delete a Document and everything about it: stored file, chunks, index entries (every revision), locators, restrictions."""
    document = DocumentRepository(session).get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    named = audit_form(document.source_uri) if is_url_document(document) else document.source_uri  # a page's query string may carry a token
    ids, details = (document.id, document.knowledge_base_id), {"file_name": named, "chunk_count": document.chunk_count}
    try:
        # The index first and verified: a deleted document must stop being answerable, so if its chunks cannot be removed it stays.
        remove_document(session, opensearch, document)
    except IndexNotUpdated as exc:
        _audit(request, principal, audit, "delete", *ids, outcome=AuditOutcome.ERROR.value, **details)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _audit(request, principal, audit, "delete", *ids, **details)
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

    # The database first, so an ingestion finishing right now re-reads the new restriction and fixes the chunks it just
    # indexed; then the index. If the index cannot be updated the old restriction is put back, so a restriction is never
    # reported that is not in force.
    repo = RestrictionRepository(session)
    previous = [(r.principal_type, r.principal_value) for r in repo.list_for_document(document.id)]
    rows = repo.replace_for_document(document.id, entries, created_by=principal.subject)
    try:
        updated = opensearch.set_document_restrictions(str(document.id), keys)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not update the chunks of document %s: %s", document.id, exc)
        repo.replace_for_document(document.id, previous, created_by=principal.subject)
        raise HTTPException(
            status_code=502, detail="The search index could not be updated, so the restriction was not changed. Try again."
        ) from exc
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
