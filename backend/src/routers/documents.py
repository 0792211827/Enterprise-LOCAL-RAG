"""Document management + ingestion endpoints."""
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from src.dependencies import DatabaseDep, IngestionServiceDep, SessionDep, SettingsDep
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.repositories import (
    DocumentRepository,
    IngestionJobRepository,
    KnowledgeBaseRepository,
)
from src.schemas.api.domain import DocumentResponse, IngestionJobResponse

router = APIRouter(prefix="/api/v1", tags=["documents"])

_TEXT_CONTENT_TYPES = {"text/plain", "text/markdown", "text/html", "application/json"}


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


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

    raw = await file.read()
    checksum = hashlib.sha256(raw).hexdigest()

    doc_repo = DocumentRepository(session)
    existing = doc_repo.get_by_checksum(kb_id, checksum)
    if existing:
        raise HTTPException(status_code=409, detail="Identical document already exists in this knowledge base")

    content_type = file.content_type or "application/octet-stream"
    raw_text = None
    file_path = None
    if content_type in _TEXT_CONTENT_TYPES or (file.filename or "").endswith((".txt", ".md")):
        raw_text = _decode_text(raw)
    else:
        # Persist binary uploads (e.g. PDFs) to a storage path for parsing.
        storage_dir = os.path.join(tempfile.gettempdir(), "rag-uploads", str(kb_id))
        os.makedirs(storage_dir, exist_ok=True)
        file_path = os.path.join(storage_dir, f"{checksum}-{file.filename or 'document'}")
        with open(file_path, "wb") as handle:
            handle.write(raw)

    document = doc_repo.create(
        Document(
            knowledge_base_id=kb_id,
            title=title or file.filename or "Untitled document",
            source_uri=file.filename,
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


@router.post("/documents/{document_id}/reprocess", response_model=IngestionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    document_id: UUID,
    session: SessionDep,
    database: DatabaseDep,
    ingestion_service: IngestionServiceDep,
    background_tasks: BackgroundTasks,
):
    """Re-run the ingestion pipeline for an existing document."""
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Clear previous chunks + reset status.
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
def delete_document(document_id: UUID, session: SessionDep):
    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_repo.delete(document)
    return None


@router.get("/ingestion-jobs", response_model=List[IngestionJobResponse])
def list_ingestion_jobs(session: SessionDep, limit: int = 100, offset: int = 0):
    return IngestionJobRepository(session).get_all(limit=limit, offset=offset)


@router.get("/documents/{document_id}/ingestion-job", response_model=IngestionJobResponse)
def get_document_ingestion_job(document_id: UUID, session: SessionDep):
    job = IngestionJobRepository(session).get_for_document(document_id)
    if not job:
        raise HTTPException(status_code=404, detail="No ingestion job found for document")
    return job
