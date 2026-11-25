"""Telemetry through the real app: what comes out of the in-memory exporters for a real request."""

import os

import pytest
from fastapi import HTTPException
from src.main import app

# Tests that read what was emitted need telemetry on; the suite can also be run with it off (see the conftest).
reads_telemetry = pytest.mark.skipif(
    os.environ.get("RAG_TEST_TELEMETRY", "on") == "off", reason="telemetry pinned off for this run"
)


@pytest.fixture
def failing_route():
    """A route that fails the way a bug would (unhandled) and one that fails the way a handler decides to."""

    def unhandled():
        raise RuntimeError("broken")

    def refuses():
        raise HTTPException(status_code=503, detail="not now")

    def not_found():
        raise HTTPException(status_code=404, detail="no such thing")

    def item(item_id: str):
        return {"id": item_id}

    app.add_api_route("/__telemetry/items/{item_id}", item)
    app.add_api_route("/__telemetry/unhandled", unhandled)
    app.add_api_route("/__telemetry/refuses", refuses)
    app.add_api_route("/__telemetry/not-found", not_found)
    yield
    del app.router.routes[-4:]


@reads_telemetry
class TestRequestMetrics:
    async def test_a_request_is_counted_and_timed_by_route_method_and_status(self, client, telemetry):
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        labels = {"route": "/api/v1/health", "method": "GET", "status": "200"}
        assert telemetry.value("rag.http.requests", **labels) == 1
        assert telemetry.value("rag.http.request.duration", **labels) == 1

    async def test_the_route_label_is_the_template_not_the_path(self, client, telemetry, failing_route):
        await client.get("/__telemetry/items/abc-123")
        await client.get("/no/such/route/ever")

        routes = {labels["route"] for labels, _ in telemetry.points("rag.http.requests")}
        assert routes == {"/__telemetry/items/{item_id}", "unmatched"}

    async def test_a_server_error_is_counted_as_an_error_but_a_client_error_is_not(self, client, telemetry, failing_route):
        await client.get("/__telemetry/refuses")
        await client.get("/__telemetry/not-found")
        with pytest.raises(RuntimeError):
            await client.get("/__telemetry/unhandled")

        assert telemetry.value("rag.http.request.errors", route="/__telemetry/refuses", status="503") == 1
        assert telemetry.value("rag.http.request.errors", route="/__telemetry/unhandled", status="500") == 1
        assert telemetry.value("rag.http.request.errors", route="/__telemetry/not-found") == 0
        assert telemetry.value("rag.http.requests", route="/__telemetry/not-found", status="404") == 1


@reads_telemetry
class TestHostileRequestId:
    async def test_a_caller_chosen_request_id_is_on_the_span_and_never_a_metric_label(self, client, telemetry, failing_route):
        hostile = "attacker-supplied-0123456789"

        response = await client.get("/__telemetry/items/a", headers={"X-Request-ID": hostile})

        assert response.headers["X-Request-ID"] == hostile
        assert telemetry.span_named("GET /__telemetry/items/{item_id}").attributes["request_id"] == hostile
        metric_labels = [value for where, _, value in telemetry.labels_and_attributes() if where.startswith("metric")]
        assert hostile not in metric_labels


class TestTelemetryOff:
    @pytest.fixture
    def telemetry(self):
        from src.config import Settings
        from src.services.telemetry import make_telemetry

        return make_telemetry(Settings())

    async def test_nothing_is_started_and_the_response_is_unchanged(self, client, telemetry, failing_route):
        from opentelemetry import trace

        def in_a_span() -> dict:
            return {"in_a_span": trace.get_current_span().get_span_context().is_valid}

        app.add_api_route("/__telemetry/probe", in_a_span)
        try:
            response = await client.get("/__telemetry/probe")
        finally:
            del app.router.routes[-1]

        assert telemetry.enabled is False
        assert response.status_code == 200 and response.json() == {"in_a_span": False}
        assert "X-Request-ID" in response.headers


@reads_telemetry
class TestTelemetryOn:
    async def test_the_same_probe_sees_the_request_span(self, client, telemetry, failing_route):
        from opentelemetry import trace

        def in_a_span() -> dict:
            return {"in_a_span": trace.get_current_span().get_span_context().is_valid}

        app.add_api_route("/__telemetry/probe", in_a_span)
        try:
            response = await client.get("/__telemetry/probe")
        finally:
            del app.router.routes[-1]

        assert response.json() == {"in_a_span": True}


@reads_telemetry
class TestFailingExporter:
    @pytest.fixture
    def telemetry(self):
        from opentelemetry.sdk.trace.export import SpanExporter
        from src.services.telemetry import InMemoryTelemetry

        class Down(SpanExporter):
            def export(self, spans):
                raise ConnectionError("collector unreachable")

        return InMemoryTelemetry(span_exporter=Down())

    async def test_an_exporter_that_raises_leaves_the_response_unchanged_and_warns_once(self, client, telemetry, caplog):
        with caplog.at_level("WARNING"):
            first = await client.get("/api/v1/health")
            second = await client.get("/api/v1/health")
            telemetry.shutdown()

        assert first.status_code == second.status_code == 200
        assert telemetry.export_state == "failing"
        dropped = [r for r in caplog.records if "is being dropped" in r.getMessage()]
        assert len(dropped) == 1


@reads_telemetry
class TestAnswerCache:
    async def test_cache_hits_and_misses_are_counted(self, client, telemetry):
        from unittest.mock import AsyncMock, MagicMock

        from src.dependencies import get_cache_client
        from src.schemas.api.ask import AskResponse

        cached = AskResponse(query="q", answer="cached", sources=[], chunks_used=1, search_mode="hybrid")
        cache = MagicMock()
        cache.find_cached_response = AsyncMock(side_effect=[None, cached])
        cache.store_response = AsyncMock()
        app.dependency_overrides[get_cache_client] = lambda: cache
        try:
            await client.post("/api/v1/ask", json={"query": "q"})
            second = await client.post("/api/v1/ask", json={"query": "q"})
        finally:
            app.dependency_overrides.pop(get_cache_client, None)

        assert second.json()["answer"] == "cached"
        assert telemetry.value("rag.cache.lookups", outcome="miss") == 1
        assert telemetry.value("rag.cache.lookups", outcome="hit") == 1
