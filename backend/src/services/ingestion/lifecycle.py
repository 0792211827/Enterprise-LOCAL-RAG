"""Removing Documents: everything about one (or a whole Knowledge Base) goes, and the index is checked.

The API, the Connector and the Knowledge Base delete all use these, so "deleted" means the same everywhere: stored file,
chunk rows, locators, restrictions and jobs from the database, and every chunk -- of every revision, staged or not --
from the search index. The index goes first and is verified empty; if it cannot be emptied nothing else is touched, so
a Document is never reported deleted while it can still be answered from.
"""

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any, Iterable, List, Optional
from uuid import UUID

from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.knowledge_base import KnowledgeBase
from src.repositories.document import DocumentRepository
from src.repositories.ingestion import IngestionJobRepository

from .types import store_file

logger = logging.getLogger(__name__)

INDEX_NOT_UPDATED = "The search index could not be updated, so nothing was changed. Try again."


class IndexNotUpdated(Exception):
    """The search index could not be emptied of a Document's (or Knowledge Base's) chunks; nothing was deleted."""


def remove_stored_file(document_repo: DocumentRepository, path: Optional[str], except_document_id: Optional[UUID] = None) -> None:
    """Delete a stored upload unless another Document still uses that very file. A file already gone is fine."""
    if not path or document_repo.file_in_use(path, except_document_id):
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove the stored file %s: %s", path, exc)


def stored_files(document: Document) -> List[str]:
    """Every file a Document holds: its current one and one waiting for a replacement."""
    waiting = ((document.extra_metadata or {}).get("replacement") or {}).get("file_path")
    return [path for path in (document.file_path, waiting) if path]


def stage_replacement(settings: Any, document_repo: DocumentRepository, document: Document, filename: str, raw: bytes, document_type: Any) -> dict:
    """Store a replacement file and record it on the Document as waiting (``queued``); the Document's own content is untouched.

    A failed earlier attempt's file is dropped. The caller queues :meth:`IngestionService.replace_document`.
    """
    checksum = hashlib.sha256(raw).hexdigest()
    stored = store_file(settings, document.knowledge_base_id, checksum, document_type, raw)
    previous = (document.extra_metadata or {}).get("replacement") or {}
    replacement = {
        "revision": uuid.uuid4().hex,
        "state": "queued",
        "filename": filename,
        "file_path": stored.file_path,
        "content_type": stored.content_type,
        "raw_text": stored.raw_text,
        "file_size_bytes": len(raw),
        "checksum": checksum,
        "error": None,
    }
    document.extra_metadata = {**(document.extra_metadata or {}), "replacement": replacement}
    if document.status == IngestionStatus.COMPLETED.value:
        document.error = None  # what was shown came from the failed attempt this one supersedes
    document_repo.save(document)
    if previous.get("file_path") and previous["file_path"] != stored.file_path:
        remove_stored_file(document_repo, previous["file_path"], document.id)
    return replacement


def delete_document(session: Any, opensearch: Any, document: Document) -> None:
    """Delete a Document and everything about it.

    :raises IndexNotUpdated: its chunks could not be removed from the index (or some remain); nothing was changed.
    """
    document_id = str(document.id)
    try:
        opensearch.purge_document_chunks(document_id)
        remaining = opensearch.count_chunks(document_id=document_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not remove the chunks of document %s from the index: %s", document_id, exc)
        raise IndexNotUpdated(INDEX_NOT_UPDATED) from exc
    if remaining:
        logger.error("%d chunks of document %s remain in the index after deleting them", remaining, document_id)
        raise IndexNotUpdated(INDEX_NOT_UPDATED)

    repo = DocumentRepository(session)
    files = stored_files(document)
    IngestionJobRepository(session).delete_for_document(document.id)
    repo.delete(document)
    for path in files:
        remove_stored_file(repo, path)


def delete_knowledge_base(session: Any, opensearch: Any, knowledge_base: KnowledgeBase, delete_row: Any) -> None:
    """Delete a Knowledge Base the way each of its Documents is deleted. ``delete_row`` removes the row itself.

    :raises IndexNotUpdated: its chunks could not be removed from the index (or some remain); nothing was changed.
    """
    kb_id = str(knowledge_base.id)
    try:
        opensearch.purge_knowledge_base_chunks(kb_id)
        remaining = opensearch.count_chunks(knowledge_base_id=kb_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not remove the chunks of knowledge base %s from the index: %s", kb_id, exc)
        raise IndexNotUpdated(INDEX_NOT_UPDATED) from exc
    if remaining:
        logger.error("%d chunks of knowledge base %s remain in the index after deleting them", remaining, kb_id)
        raise IndexNotUpdated(INDEX_NOT_UPDATED)

    repo = DocumentRepository(session)
    files: Iterable[str] = [path for document in repo.list_all_for_knowledge_base(knowledge_base.id) for path in stored_files(document)]
    IngestionJobRepository(session).delete_for_knowledge_base(knowledge_base.id)
    delete_row(knowledge_base)
    for path in files:
        remove_stored_file(repo, path)
