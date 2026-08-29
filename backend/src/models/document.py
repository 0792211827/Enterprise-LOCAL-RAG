import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import IngestionStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    """A generic enterprise document belonging to a knowledge base.

    Replaces the arXiv-specific ``Paper`` model with a source-agnostic
    abstraction that supports PDFs, DOCX, TXT, HTML, images, and other types
    through the extensible ingestion pipeline.
    """

    __tablename__ = "documents"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(
        GUID(), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title = Column(String, nullable=False)
    # Original source location (upload path, URL, share, etc.).
    source_uri = Column(String, nullable=True)
    content_type = Column(String, nullable=True)  # e.g. application/pdf, text/plain
    file_path = Column(String, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    checksum = Column(String, nullable=True, index=True)

    # Extracted content produced by the parsing/extraction stage.
    raw_text = Column(Text, nullable=True)
    sections = Column(JSON, nullable=True)

    # Ingestion lifecycle.
    status = Column(String, nullable=False, default=IngestionStatus.QUEUED.value, index=True)
    error = Column(Text, nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    processed_at = Column(DateTime, nullable=True)

    parser_used = Column(String, nullable=True)
    extra_metadata = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """Metadata for a single indexed chunk of a document.

    Embedding vectors themselves live in OpenSearch; this table persists chunk
    metadata for transparency, re-indexing, and debugging.
    """

    __tablename__ = "document_chunks"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id = Column(
        GUID(), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    word_count = Column(Integer, nullable=True)
    section_title = Column(String, nullable=True)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)

    embedding_model = Column(String, nullable=True)
    indexed = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=_utcnow)

    document = relationship("Document", back_populates="chunks")
