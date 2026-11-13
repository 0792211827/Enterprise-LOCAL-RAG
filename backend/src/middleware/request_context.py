"""Per-request correlation ID and client address.

A pure ASGI middleware (rather than ``BaseHTTPMiddleware``) so streaming
responses pass through untouched. It stores ``request_id`` and ``client_ip`` on
``request.state`` for audit records, traces and logs, and echoes the ID back as
``X-Request-ID`` so a caller can quote it when reporting a problem.
"""

import re
import uuid
from typing import Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .network import parse_networks, resolve_client_ip

REQUEST_ID_HEADER = "X-Request-ID"

# Accept a caller-supplied ID only if it is short and inert: it is echoed into
# response headers and written to logs and audit rows.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,128}")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, trusted_proxies: Sequence[str] = ()):
        self.app = app
        self.trusted = parse_networks(trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER)
        if not request_id or not _VALID_REQUEST_ID.fullmatch(request_id):
            request_id = uuid.uuid4().hex

        client = scope.get("client")
        peer = client[0] if client else None

        # Starlette's Request.state is backed by scope["state"].
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["client_ip"] = resolve_client_ip(peer, headers.get("x-forwarded-for"), self.trusted)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)
