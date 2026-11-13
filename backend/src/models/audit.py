"""The audit trail: one row per audited query, admin change or access denial."""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text
from src.db.interfaces.postgresql import Base
from src.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditAction(str, Enum):
    QUERY_ANSWER = "query.answer"  # an answer was generated (or refused) from retrieval
    QUERY_SEARCH = "query.search"  # retrieval only, no answer (hybrid search)
    ADMIN_MUTATION = "admin.mutation"  # a POST/PUT/PATCH/DELETE on the control plane
    ACCESS_DENIED = "access.denied"  # a 401/403 on any other route
    AUDIT_EXPORT = "audit.export"  # someone exported the audit trail
    AUDIT_PURGE = "audit.purge"  # an operator deleted events past retention
    UPLOADS_CLEANUP = "uploads.cleanup"  # an operator removed stale files from the upload area
    CHAT_PURGE = "chat.purge"  # Conversations past retention were deleted (counts only)


class AuditOutcome(str, Enum):
    OK = "ok"
    DENIED = "denied"  # the caller lacked access (401/403, or no permitted knowledge bases)
    ERROR = "error"


class AuditEvent(Base):
    """An append-only audit record.

    Rows are never updated by the application, and the API exposes no way to
    change or delete them (purging past retention is an operator command).
    Knowledge-base ID lists use ``None`` for "unrestricted / every knowledge
    base" and ``[]`` for "none".
    """

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_occurred_id", "occurred_at", "id"),)

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    request_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    outcome = Column(String, nullable=False)
    status_code = Column(Integer, nullable=True)

    # Who.
    actor_subject = Column(String, nullable=True, index=True)
    actor_roles = Column(JSON, nullable=True)
    actor_groups = Column(JSON, nullable=True)
    auth_method = Column(String, nullable=True)
    api_key_id = Column(GUID(), nullable=True)
    application_id = Column(GUID(), nullable=True, index=True)
    source_ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    # What was called.
    http_method = Column(String, nullable=True)
    route = Column(String, nullable=True)  # the route template, e.g. /api/v1/knowledge-bases/{kb_id}
    resource_type = Column(String, nullable=True)
    resource_id = Column(String, nullable=True)

    # What retrieval was allowed to see, and saw.
    requested_kb_ids = Column(JSON, nullable=True)
    permitted_kb_ids = Column(JSON, nullable=True)
    dropped_kb_ids = Column(JSON, nullable=True)
    retrieved = Column(JSON, nullable=True)  # [{chunk_id, document_id, knowledge_base_id, score}]
    cache_hit = Column(Boolean, nullable=True)
    model = Column(String, nullable=True)

    # The exchange itself. Text only at AUDIT__LEVEL=full; digests always.
    query_text = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=True)
    query_sha256 = Column(String, nullable=True)
    answer_sha256 = Column(String, nullable=True)

    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String, nullable=True)
    details = Column(JSON, nullable=True)  # action-specific extras (export filters, purge counts, ...)
