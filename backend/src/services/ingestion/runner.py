"""Running ingestion in the background: a few Documents at a time, each on its own, and picked up again after a restart.

Ingestion stays in the API process (no external queue). A Document waits as ``queued`` until one of the
``INGESTION__CONCURRENCY`` slots is free; one that fails, or runs past its time limit, is a failed Document and never
affects the others. A restart loses whatever was in flight, so at startup :func:`resume_interrupted` queues those
Documents again.
"""

import asyncio
import logging
import time
import uuid
import weakref
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.repositories import DocumentRepository, IngestionJobRepository, KnowledgeBaseRepository
from src.services.telemetry import NOOP_TELEMETRY

logger = logging.getLogger(__name__)

# Work started by an operator's Connector run is that run's to finish (and to retry on its next run).
CONNECTOR_PREFIX = "connector:"
IN_FLIGHT = (IngestionStatus.QUEUED.value, IngestionStatus.PROCESSING.value)
INTERRUPTED = "Interrupted by a restart."


class IngestionGate:
    """At most ``limit`` Documents are ingested at once. One semaphore per event loop (a loop's own, as asyncio needs)."""

    def __init__(self) -> None:
        self._semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()

    def slot(self, limit: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if loop not in self._semaphores:
            self._semaphores[loop] = asyncio.Semaphore(max(1, limit))
        return self._semaphores[loop]


GATE = IngestionGate()


def pending_replacement(document: Document) -> Optional[Dict[str, Any]]:
    """The replacement a Document is still waiting to ingest (queued or processing), if any."""
    replacement = (document.extra_metadata or {}).get("replacement")
    return replacement if replacement and replacement.get("state") in ("queued", "processing") else None


async def run_ingestion(
    database: Any,
    ingestion_service: Any,
    document_id: UUID,
    job_id: UUID,
    knowledge_base_id: UUID,
    concurrency: int = 2,
    replacement: bool = False,
) -> None:
    """Ingest one Document (or its pending replacement) in its own DB session, once a slot is free. Never raises."""
    queued_at = time.perf_counter()
    async with GATE.slot(concurrency):
        telemetry = getattr(ingestion_service, "telemetry", NOOP_TELEMETRY)
        telemetry.record_ingestion_wait(knowledge_base_id, time.perf_counter() - queued_at)
        try:
            with database.get_session() as session:
                doc_repo = DocumentRepository(session)
                job_repo = IngestionJobRepository(session)
                kb_repo = KnowledgeBaseRepository(session)
                document = doc_repo.get_by_id(document_id)
                job = job_repo.get_by_id(job_id)
                kb = kb_repo.get_by_id(knowledge_base_id)
                if not document or not job or not kb:
                    return
                ingest = ingestion_service.replace_document if replacement else ingestion_service.ingest_document
                await ingest(
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
        except Exception:  # noqa: BLE001 - one Document's trouble never reaches the others
            logger.exception("Ingestion of document %s ended unexpectedly", document_id)


def _abandon_replacement(ingestion_service: Any, doc_repo: DocumentRepository, document: Document, replacement: Dict[str, Any]) -> None:
    """A Connector's interrupted replacement is not resumed (its next run replaces the file again), but it must not
    show as busy for ever: what it indexed is removed and the Document shows the interruption."""
    try:
        ingestion_service.opensearch_client.purge_revision_chunks(str(document.id), replacement["revision"])
        document.extra_metadata = {**document.extra_metadata, "replacement": {**replacement, "state": "failed", "error": INTERRUPTED}}
        document.error = INTERRUPTED
        doc_repo.save(document)
    except Exception:  # noqa: BLE001
        doc_repo.rollback()
        logger.exception("Could not clear the interrupted replacement of document %s", document.id)


async def resume_interrupted(database: Any, ingestion_service: Any, concurrency: int = 2) -> int:
    """Queue again the Documents a restart left queued or processing (and replacements left unfinished).

    What a half-finished attempt indexed is removed first, so nothing is answered from twice. Returns how many were queued.
    """
    plan: List[tuple] = []
    with database.get_session() as session:
        doc_repo, job_repo = DocumentRepository(session), IngestionJobRepository(session)
        for document in list(session.scalars(select(Document))):
            replacement = pending_replacement(document)
            if (document.source_uri or "").startswith(CONNECTOR_PREFIX):
                if replacement is not None:
                    _abandon_replacement(ingestion_service, doc_repo, document, replacement)
                continue
            if replacement is not None:
                kind, revision = "replacement", replacement["revision"]
            elif document.status in IN_FLIGHT:
                kind, revision = "document", None
            else:
                continue
            try:
                if kind == "replacement":
                    ingestion_service.opensearch_client.purge_revision_chunks(str(document.id), revision)
                    document.extra_metadata = {**document.extra_metadata, "replacement": {**replacement, "state": "queued"}}
                else:
                    ingestion_service.opensearch_client.purge_document_chunks(str(document.id))
                    doc_repo.delete_chunks_for_document(document.id)
                    document.status = IngestionStatus.QUEUED.value
                    document.chunk_count = 0
                    document.error = None
                doc_repo.save(document)
                for stale in session.scalars(
                    select(IngestionJob).where(IngestionJob.document_id == document.id, IngestionJob.status.in_(IN_FLIGHT))
                ):
                    stale.status, stale.error, stale.finished_at = IngestionStatus.FAILED.value, INTERRUPTED, datetime.now(timezone.utc)
                job = job_repo.create(
                    IngestionJob(id=uuid.uuid4(), document_id=document.id, knowledge_base_id=document.knowledge_base_id, status=IngestionStatus.QUEUED.value)
                )
            except Exception:  # noqa: BLE001 - a Document that cannot be resumed stays as it is; the others go on
                session.rollback()
                logger.exception("Could not resume ingestion of document %s", document.id)
                continue
            plan.append((document.id, job.id, document.knowledge_base_id, kind == "replacement"))

    await asyncio.gather(
        *(run_ingestion(database, ingestion_service, doc, job, kb, concurrency, replacement) for doc, job, kb, replacement in plan)
    )
    if plan:
        logger.info("Resumed ingestion of %d Documents interrupted by a restart", len(plan))
    return len(plan)
