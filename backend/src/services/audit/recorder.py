"""Writing audit events.

A recorder writes each event in its **own short-lived session**, never the
request's: a request that rolls back must not erase its audit row, and an audit
write that fails must not roll back the request.
"""

import hashlib
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, ContextManager, Dict, List, Literal, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from src.models import AuditEvent

logger = logging.getLogger(__name__)

AuditLevel = Literal["off", "metadata", "full"]
FailMode = Literal["log", "deny"]


class AuditWriteError(Exception):
    """An audit event could not be written and AUDIT__FAIL_MODE=deny."""


def sha256_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class AuditRecord:
    """One event, as the application describes it (before level filtering)."""

    action: str
    outcome: str
    request_id: Optional[str] = None
    status_code: Optional[int] = None
    actor_subject: Optional[str] = None
    actor_roles: Optional[List[str]] = None
    actor_groups: Optional[List[str]] = None
    auth_method: Optional[str] = None
    api_key_id: Optional[UUID] = None
    application_id: Optional[UUID] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    http_method: Optional[str] = None
    route: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    requested_kb_ids: Optional[List[str]] = None
    permitted_kb_ids: Optional[List[str]] = None
    dropped_kb_ids: Optional[List[str]] = None
    retrieved: Optional[List[Dict[str, Any]]] = None
    cache_hit: Optional[bool] = None
    model: Optional[str] = None
    query_text: Optional[str] = None
    answer_text: Optional[str] = None
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class AuditRecorder:
    """Interface for audit recorders."""

    level: AuditLevel = "off"
    fail_mode: FailMode = "log"

    @property
    def enabled(self) -> bool:
        return self.level != "off"

    def record(self, event: AuditRecord) -> None:
        """Persist ``event``; raise ``AuditWriteError`` only when fail_mode is deny."""
        raise NotImplementedError


class NoopAuditRecorder(AuditRecorder):
    """Records nothing and never touches the database. Used when
    AUDIT__LEVEL=off, and as the fallback for apps assembled without the
    lifespan."""

    def record(self, event: AuditRecord) -> None:
        return None


class DatabaseAuditRecorder(AuditRecorder):
    """Inserts one ``audit_events`` row per event, synchronously."""

    def __init__(
        self,
        open_session: Callable[[], ContextManager[Session]],
        level: AuditLevel = "metadata",
        fail_mode: FailMode = "log",
    ):
        if level == "off":
            raise ValueError("Use NoopAuditRecorder for AUDIT__LEVEL=off")
        self.open_session = open_session
        self.level = level
        self.fail_mode = fail_mode

    def to_row(self, event: AuditRecord) -> AuditEvent:
        values = asdict(event)
        query_text = values.pop("query_text")
        answer_text = values.pop("answer_text")
        row = AuditEvent(**values)
        # Digests at every level: an auditor can prove which question was asked
        # (by hashing a candidate) without the platform storing the text.
        row.query_sha256 = sha256_text(query_text)
        row.answer_sha256 = sha256_text(answer_text)
        if self.level == "full":
            row.query_text = query_text
            row.answer_text = answer_text
        return row

    def record(self, event: AuditRecord) -> None:
        try:
            with self.open_session() as session:
                session.add(self.to_row(event))
                session.commit()
        except Exception as exc:  # noqa: BLE001 - the failure policy decides
            logger.error(
                "Failed to write audit event %s (request %s): %s", event.action, event.request_id, exc
            )
            if self.fail_mode == "deny":
                raise AuditWriteError(str(exc)) from exc
