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

    def delete(self, document: Document) -> None:
        self.session.delete(document)
        self.session.commit()

    # -- chunk operations -------------------------------------------------
    def add_chunks(self, chunks: List[DocumentChunk]) -> None:
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
