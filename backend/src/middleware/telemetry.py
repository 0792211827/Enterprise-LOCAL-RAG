"""Request metrics and the root span of each request's trace.

A pure ASGI middleware, so streaming responses pass through untouched. It sits inside ``RequestContextMiddleware``
(it records the request ID on the span, never as a metric label) and outside everything else, so the measured time
is the whole request. Nothing here can change a response: the telemetry object never raises, and the status is
only observed on its way out.
"""

from src.services.telemetry import NOOP_TELEMETRY
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class TelemetryMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = None
        if scope["type"] == "http":
            telemetry = getattr(getattr(scope.get("app"), "state", None), "telemetry", None) or NOOP_TELEMETRY
            request = telemetry.start_request(scope["method"], scope.get("state", {}).get("request_id"))
        if request is None:
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
            # The router stores the matched route on the (shared) scope; None when no route matched.
            request.finish(getattr(scope.get("route"), "path", None), status)
