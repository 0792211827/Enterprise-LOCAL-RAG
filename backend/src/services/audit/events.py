"""Building audit events from requests and retrieval results.

Answer paths call ``record_query_event`` explicitly -- a middleware cannot see
which knowledge bases were permitted or which chunks came back. Everything is
reduced to plain values up front (``AuditContext.capture``), so streaming
generators can record after the request handler has returned.
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from fastapi import Request
from src.auth import Principal
from src.models import AuditOutcome
from src.services.access import ScopeDecision

from .recorder import AuditRecord, AuditRecorder


@dataclass(frozen=True)
class AuditContext:
    """Who is calling, from where, and on which route -- as plain values."""

    request_id: Optional[str] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    http_method: Optional[str] = None
    route: Optional[str] = None
    actor_subject: Optional[str] = None
    actor_roles: Optional[List[str]] = None
    actor_groups: Optional[List[str]] = None
    auth_method: Optional[str] = None
    api_key_id: Optional[UUID] = None
    application_id: Optional[UUID] = None

    @classmethod
    def capture(cls, request: Request, principal: Optional[Principal] = None) -> "AuditContext":
        """Snapshot ``request``; ``principal`` defaults to the one auth stored on it."""
        state = request.state
        principal = principal or getattr(state, "principal", None)
        route = request.scope.get("route")
        return cls(
            request_id=getattr(state, "request_id", None),
            source_ip=getattr(state, "client_ip", None),
            user_agent=request.headers.get("user-agent"),
            http_method=request.method,
            route=getattr(route, "path", None) or request.url.path,
            **principal_fields(principal),
        )


def principal_fields(principal: Optional[Principal]) -> Dict[str, Any]:
    if principal is None:
        return {}
    return {
        "actor_subject": principal.subject,
        "actor_roles": sorted(role.value for role in principal.roles),
        "actor_groups": sorted(principal.groups),
        "auth_method": principal.auth_method,
        "api_key_id": principal.api_key_id,
        "application_id": principal.application_id,
    }


def retrieved_from_hits(hits: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The identifying facts of each retrieved chunk -- never its text."""
    retrieved = []
    for hit in hits or []:
        document_id = hit.get("document_id")
        knowledge_base_id = hit.get("knowledge_base_id")
        score = hit.get("score")
        retrieved.append(
            {
                "chunk_id": hit.get("chunk_id"),
                "document_id": str(document_id) if document_id else None,
                "knowledge_base_id": str(knowledge_base_id) if knowledge_base_id else None,
                "score": float(score) if isinstance(score, (int, float)) else None,
            }
        )
    return retrieved


def decision_outcome(decision: Optional[ScopeDecision]) -> str:
    """``denied`` when the caller wanted knowledge they may not read and got none.

    An application with no knowledge bases attached searches nothing too, but
    that is a configuration gap, not a denial.
    """
    if decision is not None and decision.permitted == [] and (decision.requested is None or decision.dropped):
        return AuditOutcome.DENIED.value
    return AuditOutcome.OK.value


def elapsed_ms(started: float) -> int:
    """Milliseconds since ``started`` (a ``time.perf_counter()`` value)."""
    return int((time.perf_counter() - started) * 1000)


def record_query_event(
    recorder: AuditRecorder,
    context: AuditContext,
    *,
    action: str,
    query: Optional[str],
    answer: Optional[str] = None,
    decision: Optional[ScopeDecision] = None,
    hits: Optional[Iterable[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    outcome: Optional[str] = None,
    status_code: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error_code: Optional[str] = None,
    application_id: Optional[UUID] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Record one query or search.

    :param decision: the knowledge-base scope applied; its ``permitted=None``
        is recorded as NULL, meaning unrestricted.
    :param outcome: defaults to ``denied``/``ok`` from ``decision``.
    :raises AuditWriteError: only when the write fails and fail_mode is deny.
    """
    if not recorder.enabled:
        return
    recorder.record(
        AuditRecord(
            action=action,
            outcome=outcome or decision_outcome(decision),
            request_id=context.request_id,
            status_code=status_code,
            actor_subject=context.actor_subject,
            actor_roles=context.actor_roles,
            actor_groups=context.actor_groups,
            auth_method=context.auth_method,
            api_key_id=context.api_key_id,
            application_id=application_id or context.application_id,
            source_ip=context.source_ip,
            user_agent=context.user_agent,
            http_method=context.http_method,
            route=context.route,
            requested_kb_ids=decision.requested if decision else None,
            permitted_kb_ids=decision.permitted if decision else None,
            dropped_kb_ids=decision.dropped if decision else None,
            retrieved=retrieved_from_hits(hits),
            cache_hit=cache_hit,
            model=model,
            query_text=query,
            answer_text=answer,
            latency_ms=latency_ms,
            error_code=error_code,
            details=details,
        )
    )
