import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import RetrievalMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase(Base):
    """A logical collection of enterprise documents with its own index.

    A knowledge base owns its documents/chunks and its retrieval + embedding
    configuration. Each knowledge base maps to a dedicated OpenSearch index so
    collections remain isolated within a single deployment.
    """

    __tablename__ = "knowledge_bases"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Index + embedding configuration (local-first, provider-agnostic).
    index_name = Column(String, nullable=False)
    embedding_provider = Column(String, nullable=False, default="huggingface")
    embedding_model = Column(String, nullable=False, default="BAAI/bge-m3")
    embedding_dimension = Column(Integer, nullable=False, default=1024)

    # Default retrieval configuration for this knowledge base.
    retrieval_mode = Column(String, nullable=False, default=RetrievalMode.HYBRID.value)
    default_top_k = Column(Integer, nullable=False, default=10)

    document_count = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)

    extra_metadata = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")
