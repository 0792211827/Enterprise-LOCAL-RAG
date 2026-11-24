"""A local web server for URL fetching tests: nothing reaches the internet. It serves what each test registers and records
every request, so a test can also assert that a request was never made."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

import pytest


class Site:
    def __init__(self) -> None:
        self.routes: Dict[str, Tuple[int, Dict[str, str], bytes, float]] = {}
        self.requests: List[Tuple[str, Dict[str, str]]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                site.requests.append((self.path, dict(self.headers)))
                status, headers, body, delay = site.routes.get(self.path, (404, {}, b"not found", 0))
                if delay:
                    threading.Event().wait(delay)
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        if name.lower() != "content-length":
                            self.send_header(name, value)
                    self.send_header("Content-Length", str(headers.get("Content-Length", len(body))))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):  # silence
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def serve(self, path: str, body: bytes, content_type: str = "text/html", status: int = 200, delay: float = 0, **headers: str) -> None:
        self.routes[path] = (status, {"Content-Type": content_type, **headers}, body, delay)

    def redirect(self, path: str, to: str, status: int = 302) -> None:
        self.routes[path] = (status, {"Location": to}, b"", 0)

    def url(self, path: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def paths(self) -> List[str]:
        return [path for path, _ in self.requests]

    def header_seen(self, path: str, name: str) -> Optional[str]:
        return next((headers.get(name) for p, headers in self.requests if p == path), None)


@pytest.fixture
def site():
    stub = Site()
    stub.thread.start()
    yield stub
    stub.server.shutdown()
    stub.server.server_close()


def resolving(table: Dict[str, List[str]], then: Optional[Dict[str, List[str]]] = None):
    """A resolver for tests: what each name resolves to (and, on later calls, to ``then``); it records every call."""
    calls: List[str] = []

    async def resolver(host: str, port: int) -> List[str]:
        calls.append(host)
        answers = then if then and len([c for c in calls if c == host]) > 1 else table
        if host not in answers:
            raise OSError(f"no such host {host}")
        return answers[host]

    resolver.calls = calls  # type: ignore[attr-defined]
    return resolver
