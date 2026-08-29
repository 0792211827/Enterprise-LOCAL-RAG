import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import RetrievalMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RetrievalConfiguration(Base):
    """Reusable retrieval settings for an application or knowledge base.

    Exposes administrator-meaningful controls over BM25 / vector / hybrid
    retrieval and RRF fusion without leaking internal implementation details.
    """

    __tablename__ = "retrieval_configurations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)

    mode = Column(String, nullable=False, default=RetrievalMode.HYBRID.value)
    top_k = Column(Integer, nullable=False, default=10)

    # Hybrid search + RRF fusion controls.
    hybrid_size_multiplier = Column(Integer, nullable=False, default=2)
    rrf_rank_constant = Column(Integer, nullable=False, default=60)

    # Optional relevance gate and metadata filters.
    score_threshold = Column(Float, nullable=True)
    filters = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
