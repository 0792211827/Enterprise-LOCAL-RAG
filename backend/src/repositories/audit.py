"""Reading (and purging past retention) the audit trail. There is no update."""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import String, and_, cast, delete, or_, select
from sqlalchemy.orm import Session
from src.models import AuditEvent

Position = Tuple[datetime, UUID]


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC (SQLite drops the offset on read)."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def encode_cursor(event: AuditEvent) -> str:
    raw = f"{as_utc(event.occurred_at).isoformat()}|{event.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> Position:
    """:raises ValueError: for a cursor this API did not issue."""
    try:
        occurred_at, _, event_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return as_utc(datetime.fromisoformat(occurred_at)), UUID(event_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid cursor") from exc


@dataclass(frozen=True)
class AuditFilters:
    actor: Optional[str] = None
    action: Optional[str] = None
    outcome: Optional[str] = None
    application_id: Optional[UUID] = None
    knowledge_base_id: Optional[UUID] = None
    request_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None

    def as_dict(self) -> dict:
        return {k: str(v) for k, v in self.__dict__.items() if v is not None}


class AuditEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, event_id: UUID) -> Optional[AuditEvent]:
        return self.session.get(AuditEvent, event_id)

    def page(self, filters: AuditFilters, limit: int, after: Optional[Position] = None) -> List[AuditEvent]:
        """Newest first. ``after`` is the (occurred_at, id) of the last row already seen."""
        query = select(AuditEvent).where(*self._conditions(filters))
        if after is not None:
            occurred_at, event_id = after
            query = query.where(
                or_(
                    AuditEvent.occurred_at < occurred_at,
                    and_(AuditEvent.occurred_at == occurred_at, AuditEvent.id < event_id),
                )
            )
        query = query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit)
        return list(self.session.scalars(query))

    def purge_before(self, cutoff: datetime) -> int:
        result = self.session.execute(delete(AuditEvent).where(AuditEvent.occurred_at < cutoff))
        self.session.commit()
        return result.rowcount or 0

    @staticmethod
    def _conditions(filters: AuditFilters) -> list:
        conditions = []
        if filters.actor:
            conditions.append(AuditEvent.actor_subject == filters.actor)
        if filters.action:
            conditions.append(AuditEvent.action == filters.action)
        if filters.outcome:
            conditions.append(AuditEvent.outcome == filters.outcome)
        if filters.application_id:
            conditions.append(AuditEvent.application_id == filters.application_id)
        if filters.request_id:
            conditions.append(AuditEvent.request_id == filters.request_id)
        if filters.since:
            conditions.append(AuditEvent.occurred_at >= as_utc(filters.since))
        if filters.until:
            conditions.append(AuditEvent.occurred_at < as_utc(filters.until))
        if filters.knowledge_base_id:
            # Text match over the JSON columns: portable across PostgreSQL and
            # SQLite. Safe because the value is a parsed UUID (no LIKE wildcards).
            needle = f'%"{filters.knowledge_base_id}"%'
            conditions.append(
                or_(
                    *(
                        cast(column, String).like(needle)
                        for column in (
                            AuditEvent.requested_kb_ids,
                            AuditEvent.permitted_kb_ids,
                            AuditEvent.dropped_kb_ids,
                            AuditEvent.retrieved,
                        )
                    )
                )
            )
        return conditions
