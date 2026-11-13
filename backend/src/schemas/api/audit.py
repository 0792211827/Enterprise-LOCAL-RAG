from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditStatus(BaseModel):
    """How the audit trail is configured, so the UI can say whether it's recording."""

    level: str = Field(description="off | metadata | full")
    fail_mode: str = Field(description="log | deny")
    retention_days: int = Field(description="Age at which `make audit-purge` deletes events; 0 = keep forever")


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    request_id: Optional[str] = None
    action: str
    outcome: str
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
    requested_kb_ids: Optional[List[str]] = Field(None, description="NULL: a global search (every knowledge base)")
    permitted_kb_ids: Optional[List[str]] = Field(None, description="NULL: unrestricted; []: none")
    dropped_kb_ids: Optional[List[str]] = None
    retrieved: Optional[List[Dict[str, Any]]] = Field(None, description="NULL: no retrieval ran (e.g. a cache hit)")
    cache_hit: Optional[bool] = None
    model: Optional[str] = None
    query_text: Optional[str] = Field(None, description="Only at AUDIT__LEVEL=full")
    answer_text: Optional[str] = Field(None, description="Only at AUDIT__LEVEL=full")
    query_sha256: Optional[str] = None
    answer_sha256: Optional[str] = None
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class AuditEventPage(BaseModel):
    items: List[AuditEventResponse]
    next_cursor: Optional[str] = Field(None, description="Pass as `cursor` for the next (older) page; null at the end")
