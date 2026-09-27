"""Building audit events from requests and retrieval results.

Answer paths call ``record_query_event`` explicitly -- a middleware cannot see
which knowledge bases were permitted or which chunks came back. Everything is
reduced to plain values up front (``AuditContext.capture``), so streaming
generators can record after the request handler has returned.
"""

import asyncio
import logging
import time
from contextlib import aclosing, contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional
from uuid import UUID

from fastapi import HTTPException, Request
from src.auth import Principal
from src.models import AuditOutcome
from src.services.access import ScopeDecision

from .recorder import AuditRecord, AuditRecorder, AuditWriteError

logger = logging.getLogger(__name__)

AUDIT_UNAVAILABLE_MESSAGE = "The audit log is unavailable, so this request was refused."


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


def record_event(
    recorder: AuditRecorder,
    context: AuditContext,
    *,
    action: str,
    outcome: str = AuditOutcome.OK.value,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a non-query event (e.g. an export) attributed to ``context``.

    :raises AuditWriteError: only when the write fails and fail_mode is deny.
    """
    if not recorder.enabled:
        return
    recorder.record(
        AuditRecord(
            action=action,
            outcome=outcome,
            request_id=context.request_id,
            actor_subject=context.actor_subject,
            actor_roles=context.actor_roles,
            actor_groups=context.actor_groups,
            auth_method=context.auth_method,
            api_key_id=context.api_key_id,
            application_id=context.application_id,
            source_ip=context.source_ip,
            user_agent=context.user_agent,
            http_method=context.http_method,
            route=context.route,
            details=details,
        )
    )


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
    :param hits: what retrieval returned; ``None`` (recorded as NULL) means
        retrieval did not run here, e.g. a cache hit.
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
            retrieved=retrieved_from_hits(hits) if hits is not None else None,
            cache_hit=cache_hit,
            model=model,
            query_text=query,
            answer_text=answer,
            latency_ms=latency_ms,
            error_code=error_code,
            details=details,
        )
    )


def error_code_for(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        return f"http_{exc.status_code}"
    if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
        return "client_disconnected"
    return type(exc).__name__


class QueryAudit:
    """Collects one query's audit facts while it runs, and records them once.

    The answer path fills in ``answer``, ``hits``, ``model`` and so on as they
    become known; whichever way the request ends -- a normal return, an early
    return, an exception, a client disconnect -- exactly one event is written.
    """

    def __init__(
        self,
        recorder: AuditRecorder,
        context: AuditContext,
        *,
        action: str,
        query: Optional[str],
        decision: Optional[ScopeDecision] = None,
        application_id: Optional[UUID] = None,
    ):
        self.recorder = recorder
        self.context = context
        self.action = action
        self.query = query
        self.decision = decision
        self.application_id = application_id
        self.answer: Optional[str] = None
        self.hits: Optional[Iterable[Dict[str, Any]]] = None
        self.model: Optional[str] = None
        self.cache_hit: Optional[bool] = None
        self.outcome: Optional[str] = None
        self.error_code: Optional[str] = None
        self.details: Optional[Dict[str, Any]] = None
        self._started = time.perf_counter()
        self._recorded = False

    def fail(self, exc: BaseException) -> None:
        """Mark the query as failed (for paths that handle their own errors)."""
        self.outcome = "error"
        self.error_code = error_code_for(exc)

    def record(self) -> None:
        """Write the event, once. Raises ``AuditWriteError`` under fail_mode=deny."""
        if self._recorded:
            return
        self._recorded = True
        record_query_event(
            self.recorder,
            self.context,
            action=self.action,
            query=self.query,
            answer=self.answer,
            decision=self.decision,
            hits=self.hits,
            model=self.model,
            cache_hit=self.cache_hit,
            outcome=self.outcome,
            latency_ms=elapsed_ms(self._started),
            error_code=self.error_code,
            application_id=self.application_id,
            details=self.details,
        )

    def record_after_abort(self, exc: BaseException) -> None:
        """Record a request that is already failing; never mask its exception."""
        if self.outcome is None:
            self.fail(exc)
        try:
            self.record()
        except AuditWriteError:
            pass  # already logged by the recorder; the original error wins


@contextmanager
def audited_query(recorder: AuditRecorder, context: AuditContext, **fields: Any) -> Iterator[QueryAudit]:
    """Audit a request handler: records on exit, including on exceptions.

    Under fail_mode=deny a failed write on the success path raises
    ``AuditWriteError`` (rendered as 503), so an unaudited answer is not served.
    """
    audit = QueryAudit(recorder, context, **fields)
    try:
        yield audit
    except BaseException as exc:
        audit.record_after_abort(exc)
        raise
    audit.record()


async def audited_stream(
    events: AsyncIterator[str],
    audit: QueryAudit,
    error_frame: Callable[[str], str],
    trailer: Optional[str] = None,
) -> AsyncIterator[str]:
    """Pass a streaming body through, recording its audit event at the end.

    The event is written after the last content frame and before ``trailer``
    (e.g. OpenAI's ``[DONE]``). A stream has already delivered its answer by
    then, so under fail_mode=deny a failed write can only append an error frame.
    """
    try:
        async with aclosing(events) as frames:
            async for frame in frames:
                yield frame
    except BaseException as exc:
        audit.record_after_abort(exc)
        raise
    try:
        audit.record()
    except AuditWriteError:
        yield error_frame(AUDIT_UNAVAILABLE_MESSAGE)
    if trailer is not None:
        yield trailer
