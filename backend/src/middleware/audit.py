"""Audit events for control-plane changes and access denials.

Query endpoints record their own, richer events (retrieval scope, chunk IDs);
everything else is covered here without touching the ~20 CRUD handlers:

* ``admin.mutation`` -- any POST/PUT/PATCH/DELETE under ``/api/v1`` that is
  not a query endpoint, with the route template, path parameters, the real
  status code and the caller;
* ``access.denied`` -- any other request answered 401 or 403 (a querier
  probing the audit API, a bad API key on ``/v1``, ...).

A pure ASGI middleware, so streaming responses pass through untouched. It runs
inside ``RequestContextMiddleware`` (for the request ID and client IP) and
reads the caller from ``request.state.principal``, which authentication sets.
(Not re-exported from ``src.middleware``: that package is imported by
``src.auth``, which this module needs.)
The response has already been sent when the event is written, so a failed
write is logged and never changes it, whatever AUDIT__FAIL_MODE says.
"""

from typing import Any, Dict, Optional

from src.models import AuditAction, AuditOutcome
from src.services.audit import AuditRecord, AuditWriteError, principal_fields
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Recorded by the handlers themselves (query events), or not a control-plane
# change (feedback on an answer).
SELF_AUDITED_ROUTES = frozenset(
    {
        "/api/v1/ask",
        "/api/v1/stream",
        "/api/v1/ask-agentic",
        "/api/v1/hybrid-search/",
        "/api/v1/applications/{application_id}/ask",
        "/api/v1/feedback",
        "/v1/chat/completions",
    }
)


def resource_type_of(route: str) -> Optional[str]:
    """``/api/v1/knowledge-bases/{kb_id}/grants`` -> ``knowledge-bases``."""
    parts = [p for p in route.split("/") if p]
    return parts[2] if len(parts) > 2 and parts[:2] == ["api", "v1"] else None


def classify(method: str, route: Optional[str], status: int) -> Optional[tuple[str, str]]:
    """``(action, outcome)`` for a finished request, or None if it is not audited here."""
    mutation = (
        method in MUTATING_METHODS
        and route is not None
        and route.startswith("/api/v1/")
        and route not in SELF_AUDITED_ROUTES
    )
    if status in (401, 403):
        return (AuditAction.ADMIN_MUTATION if mutation else AuditAction.ACCESS_DENIED).value, AuditOutcome.DENIED.value
    if mutation:
        return AuditAction.ADMIN_MUTATION.value, (AuditOutcome.OK if status < 400 else AuditOutcome.ERROR).value
    return None


class AuditMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        recorder = getattr(getattr(scope.get("app"), "state", None), "audit_recorder", None)
        if scope["type"] != "http" or recorder is None or not recorder.enabled:
            await self.app(scope, receive, send)
            return

        status = 500  # if the app raises before responding

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            self._record(recorder, scope, status)

    @staticmethod
    def _record(recorder: Any, scope: Scope, status: int) -> None:
        # The router stores the matched route on the (shared) scope.
        route = getattr(scope.get("route"), "path", None)
        method = scope["method"]
        classified = classify(method, route, status)
        if classified is None:
            return
        action, outcome = classified

        state: Dict[str, Any] = scope.get("state", {})
        path_params: Dict[str, Any] = {k: str(v) for k, v in (scope.get("path_params") or {}).items()}
        try:
            recorder.record(
                AuditRecord(
                    action=action,
                    outcome=outcome,
                    status_code=status,
                    request_id=state.get("request_id"),
                    source_ip=state.get("client_ip"),
                    user_agent=Headers(scope=scope).get("user-agent"),
                    http_method=method,
                    route=route or scope.get("path"),
                    resource_type=resource_type_of(route) if route else None,
                    resource_id=next(iter(path_params.values()), None),
                    details={"path_params": path_params} if path_params else None,
                    **principal_fields(state.get("principal")),
                )
            )
        except AuditWriteError:
            pass  # logged by the recorder; the response has already been sent
