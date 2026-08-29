import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import relationship
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import RAGStrategy


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Many-to-many: an application can span multiple knowledge bases.
application_knowledge_bases = Table(
    "application_knowledge_bases",
    Base.metadata,
    Column(
        "application_id",
        GUID(),
        ForeignKey("rag_applications.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "knowledge_base_id",
        GUID(),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class RAGApplication(Base):
    """A configurable RAG application exposed to end users.

    Each application binds one or more knowledge bases to a model
    configuration, a system prompt, retrieval settings, and a RAG strategy
    (traditional or agentic). All configuration is stored rather than
    hardcoded.
    """

    __tablename__ = "rag_applications"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)

    system_prompt = Column(Text, nullable=True)
    rag_strategy = Column(String, nullable=False, default=RAGStrategy.TRADITIONAL.value)

    # Model + retrieval configuration references.
    model_configuration_id = Column(
        GUID(), ForeignKey("model_configurations.id", ondelete="SET NULL"), nullable=True
    )
    retrieval_configuration_id = Column(
        GUID(), ForeignKey("retrieval_configurations.id", ondelete="SET NULL"), nullable=True
    )

    streaming_enabled = Column(Boolean, nullable=False, default=True)
    citations_enabled = Column(Boolean, nullable=False, default=True)

    extra_metadata = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    knowledge_bases = relationship("KnowledgeBase", secondary=application_knowledge_bases)
    model_configuration = relationship("ModelConfiguration")
    retrieval_configuration = relationship("RetrievalConfiguration")
