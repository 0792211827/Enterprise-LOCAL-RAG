import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import IngestionStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngestionJob(Base):
    """Tracks the progress of a document through the ingestion pipeline.

    Provides the admin UI with per-stage status (parse → extract → chunk →
    embed → index) and error visibility.
    """

    __tablename__ = "ingestion_jobs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id = Column(
        GUID(), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status = Column(String, nullable=False, default=IngestionStatus.QUEUED.value, index=True)
    stage = Column(String, nullable=True)  # parse | extract | chunk | embed | index
    error = Column(Text, nullable=True)
    stats = Column(JSON, nullable=True)  # chunks_created, embeddings_generated, ...

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
