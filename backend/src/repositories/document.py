from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.document import Document, DocumentChunk


class DocumentRepository:
    """Persistence operations for documents and their chunks."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, document: Document) -> Document:
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    def get_by_id(self, document_id: UUID) -> Optional[Document]:
        return self.session.get(Document, document_id)

    def get_by_checksum(self, knowledge_base_id: UUID, checksum: str) -> Optional[Document]:
        stmt = select(Document).where(
            Document.knowledge_base_id == knowledge_base_id, Document.checksum == checksum
        )
        return self.session.scalar(stmt)

    def get_by_source_name(self, knowledge_base_id: UUID, name: str) -> Optional[Document]:
        """The Document an upload of this name was made for (uploads keep their display name as ``source_uri``)."""
        stmt = select(Document).where(Document.knowledge_base_id == knowledge_base_id, Document.source_uri == name)
        return self.session.scalars(stmt.order_by(Document.created_at)).first()

    def list_for_knowledge_base(
        self, knowledge_base_id: UUID, limit: int = 100, offset: int = 0
    ) -> List[Document]:
        stmt = (
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt))

    def list_all(self, limit: int = 100, offset: int = 0) -> List[Document]:
        stmt = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def count(self, knowledge_base_id: Optional[UUID] = None) -> int:
        stmt = select(func.count()).select_from(Document)
        if knowledge_base_id is not None:
            stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)
        return self.session.scalar(stmt) or 0

    def restriction_keys(self, document_id: UUID) -> List[str]:
        """The Principals the document is restricted to, as stored on its chunks; empty when it is unrestricted."""
        from src.repositories.restrictions import RestrictionRepository

        return RestrictionRepository(self.session).keys_for_document(document_id)

    def update_status(self, document: Document, status: str, error: Optional[str] = None) -> Document:
        document.status = status
        if error is not None:
            document.error = error
        self.session.commit()
        self.session.refresh(document)
        return document

    def save(self, document: Document) -> Document:
        self.session.commit()
        self.session.refresh(document)
        return document

    def exists(self, document_id: UUID) -> bool:
        """Whether the Document is still there, read from the database (not the session's cache)."""
        return bool(self.session.scalar(select(func.count()).select_from(Document).where(Document.id == document_id)))

    def rollback(self) -> None:
        self.session.rollback()

    def file_in_use(self, path: str, except_document_id: Optional[UUID] = None) -> bool:
        """Whether a Document other than ``except_document_id`` holds (or awaits a replacement with) this stored file."""
        stmt = select(Document).where(Document.file_path == path)
        if except_document_id is not None:
            stmt = stmt.where(Document.id != except_document_id)
        if self.session.scalar(stmt.limit(1)) is not None:
            return True
        waiting = select(Document.extra_metadata).where(Document.extra_metadata.is_not(None))
        if except_document_id is not None:
            waiting = waiting.where(Document.id != except_document_id)
        return any(((meta or {}).get("replacement") or {}).get("file_path") == path for meta in self.session.scalars(waiting))

    def list_all_for_knowledge_base(self, knowledge_base_id: UUID) -> List[Document]:
        return list(self.session.scalars(select(Document).where(Document.knowledge_base_id == knowledge_base_id)))

    def delete(self, document: Document) -> None:
        self.session.delete(document)
        self.session.commit()

    # -- chunk operations -------------------------------------------------
    def add_chunks(self, chunks: List[DocumentChunk]) -> None:
        self.session.add_all(chunks)
        self.session.commit()

    def replace_chunks(self, document_id: UUID, chunks: List[DocumentChunk]) -> None:
        """Swap a Document's chunk rows for ``chunks`` -- and commit whatever else was changed on it -- in one transaction."""
        for chunk in self.session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)):
            self.session.delete(chunk)
        self.session.flush()
        self.session.add_all(chunks)
        self.session.commit()

    def delete_chunks_for_document(self, document_id: UUID) -> None:
        for chunk in self.session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        ):
            self.session.delete(chunk)
        self.session.commit()

    def count_chunks(self, knowledge_base_id: Optional[UUID] = None) -> int:
        stmt = select(func.count()).select_from(DocumentChunk)
        if knowledge_base_id is not None:
            stmt = stmt.where(DocumentChunk.knowledge_base_id == knowledge_base_id)
        return self.session.scalar(stmt) or 0
