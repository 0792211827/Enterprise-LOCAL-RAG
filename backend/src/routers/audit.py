"""Read-only access to the audit trail, for the auditor role.

There are deliberately no update or delete endpoints: events can only be
purged past retention by an operator (``make audit-purge``). Exports are
themselves audited, before any data is sent.
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Annotated, Iterator, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from src.dependencies import AuditDep, SessionDep, SessionOpenerDep, SettingsDep
from src.models import AuditAction, AuditEvent, AuditOutcome
from src.repositories import AuditEventRepository, AuditFilters
from src.repositories.audit import as_utc, decode_cursor, encode_cursor
from src.schemas.api.audit import AuditEventPage, AuditEventResponse, AuditStatus
from src.services.audit import AuditContext, record_event

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

EXPORT_BATCH = 500
EXPORT_COLUMNS = list(AuditEventResponse.model_fields)


def audit_filters(
    actor: Optional[str] = Query(None, description="Exact subject of the caller"),
    action: Optional[AuditAction] = None,
    outcome: Optional[AuditOutcome] = None,
    application_id: Optional[UUID] = None,
    knowledge_base_id: Optional[UUID] = Query(
        None, description="Events whose requested, permitted or dropped scope, or retrieved chunks, name this KB"
    ),
    request_id: Optional[str] = None,
    since: Optional[datetime] = Query(None, description="Inclusive; naive values are UTC"),
    until: Optional[datetime] = Query(None, description="Exclusive; naive values are UTC"),
) -> AuditFilters:
    return AuditFilters(
        actor=actor,
        action=action.value if action else None,
        outcome=outcome.value if outcome else None,
        application_id=application_id,
        knowledge_base_id=knowledge_base_id,
        request_id=request_id,
        since=since,
        until=until,
    )


FiltersDep = Annotated[AuditFilters, Depends(audit_filters)]


def _serialise(event: AuditEvent) -> dict:
    response = AuditEventResponse.model_validate(event)
    response.occurred_at = as_utc(response.occurred_at)
    return response.model_dump(mode="json")


@router.get("/status", response_model=AuditStatus)
def audit_status(settings: SettingsDep):
    audit = settings.audit
    return AuditStatus(level=audit.level, fail_mode=audit.fail_mode, retention_days=audit.retention_days)


@router.get("/events", response_model=AuditEventPage)
def list_events(
    session: SessionDep,
    filters: FiltersDep,
    limit: int = Query(50, ge=1, le=500),
    cursor: Optional[str] = Query(None, description="next_cursor from the previous page"),
):
    """Events, newest first, with keyset pagination."""
    try:
        after = decode_cursor(cursor) if cursor else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    # One extra row tells us whether another page exists.
    rows = AuditEventRepository(session).page(filters, limit + 1, after)
    page = rows[:limit]
    return AuditEventPage(
        items=[_serialise(event) for event in page],
        next_cursor=encode_cursor(page[-1]) if len(rows) > limit else None,
    )


@router.get("/events/{event_id}", response_model=AuditEventResponse)
def get_event(event_id: UUID, session: SessionDep):
    event = AuditEventRepository(session).get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return _serialise(event)


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    text = str(value)
    # Spreadsheets execute cells starting with these; exports get opened in them.
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export")
def export_events(
    request: Request,
    filters: FiltersDep,
    open_session: SessionOpenerDep,
    recorder: AuditDep,
    format: Literal["ndjson", "csv"] = "ndjson",
):
    """Stream every matching event, newest first, as NDJSON or CSV."""
    # Recorded before any data leaves, so under AUDIT__FAIL_MODE=deny an
    # export that cannot be audited is refused (503) rather than sent.
    record_event(
        recorder,
        AuditContext.capture(request),
        action=AuditAction.AUDIT_EXPORT.value,
        details={"format": format, "filters": filters.as_dict()},
    )

    def batches() -> Iterator[list]:
        after = None
        while True:
            with open_session() as session:
                rows = AuditEventRepository(session).page(filters, EXPORT_BATCH, after)
                serialised = [_serialise(event) for event in rows]
            if not rows:
                return
            yield serialised
            after = (as_utc(rows[-1].occurred_at), rows[-1].id)

    def ndjson() -> Iterator[str]:
        for batch in batches():
            yield "".join(json.dumps(event) + "\n" for event in batch)

    def csv_rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        for batch in batches():
            for event in batch:
                writer.writerow([_csv_cell(event[column]) for column in EXPORT_COLUMNS])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
        yield buffer.getvalue()  # the header, when nothing matched

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body, media_type = (ndjson(), "application/x-ndjson") if format == "ndjson" else (csv_rows(), "text/csv")
    return StreamingResponse(
        body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.{format}"'},
    )
