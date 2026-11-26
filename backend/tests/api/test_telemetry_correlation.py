"""One request, followed from the Audit log to its trace to its logs (ticket 05), through the real app."""

import json
import logging
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from src.logging_setup import JsonFormatter
from src.main import app
from src.services.audit import DatabaseAuditRecorder

from tests.api.test_telemetry_application_path import (  # noqa: F401  (fixtures)
    QUESTION,
    index,
    llm,
    make_application,
    play,
    stack,
)
from tests.audit_support import events, opener, sqlite_session_factory

reads_telemetry = pytest.mark.skipif(
    os.environ.get("RAG_TEST_TELEMETRY", "on") == "off", reason="telemetry pinned off for this run"
)

ASK = "/api/v1/ask"


@pytest.fixture
def audit_factory():
    factory = sqlite_session_factory()
    previous = getattr(app.state, "audit_recorder", None)
    app.state.audit_recorder = DatabaseAuditRecorder(opener(factory), level="metadata", fail_mode="log")
    yield factory
    app.state.audit_recorder = previous


def query_events(factory):
    return [e for e in events(factory) if e.action == "query.answer"]


def hex_trace_id(span) -> str:
    return format(span.context.trace_id, "032x")


@reads_telemetry
class TestAuditEventHoldsTheTraceId:
    async def test_the_request_id_header_the_span_and_the_audit_event_agree_and_the_event_names_the_trace(
        self, stack, telemetry, audit_factory
    ):
        response = await stack.post(ASK, json={"query": QUESTION}, headers={"X-Request-ID": "auditor-ref-0001"})

        assert response.status_code == 200
        (event,) = query_events(audit_factory)
        root = telemetry.span_named(f"POST {ASK}")
        assert response.headers["X-Request-ID"] == event.request_id == root.attributes["request_id"] == "auditor-ref-0001"
        assert event.details["trace_id"] == hex_trace_id(root)

    async def test_a_generated_request_id_is_the_same_on_all_three(self, stack, telemetry, audit_factory):
        response = await stack.post(ASK, json={"query": QUESTION})

        (event,) = query_events(audit_factory)
        root = telemetry.span_named(f"POST {ASK}")
        assert response.headers["X-Request-ID"] == event.request_id == root.attributes["request_id"]

    async def test_a_streamed_answer_records_its_event_with_the_trace_too(self, stack, telemetry, audit_factory):
        await stack.post("/api/v1/stream", json={"query": QUESTION})

        (event,) = [e for e in events(audit_factory) if e.action.startswith("query.")]
        assert event.details["trace_id"] == hex_trace_id(telemetry.span_named("POST /api/v1/stream"))

    async def test_an_admin_change_is_recorded_with_the_trace_beside_its_own_details(self, stack, telemetry, audit_factory):
        await stack.post("/api/v1/knowledge-bases", json={"name": "Policies"})

        (event,) = [e for e in events(audit_factory) if e.action == "admin.mutation"]
        assert event.details["trace_id"] == hex_trace_id(telemetry.span_named("POST /api/v1/knowledge-bases"))


class TestAuditEventWithTelemetryOff:
    async def test_the_event_is_what_it_always_was(self, stack, audit_factory):
        from src.services.telemetry import NOOP_TELEMETRY

        app.state.telemetry = NOOP_TELEMETRY
        await stack.post(ASK, json={"query": QUESTION})

        (event,) = query_events(audit_factory)
        assert "trace_id" not in (event.details or {})
        assert event.request_id


@reads_telemetry
class TestFailModeIsIndependentOfTelemetry:
    @pytest.fixture
    def broken(self):
        @contextmanager
        def open_session():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        return open_session

    async def test_deny_mode_still_refuses_with_telemetry_on(self, stack, telemetry, broken):
        app.state.audit_recorder = DatabaseAuditRecorder(broken, level="metadata", fail_mode="deny")

        response = await stack.post(ASK, json={"query": QUESTION})

        assert telemetry.enabled
        assert response.status_code == 503
        assert "audit log is unavailable" in response.json()["detail"]

    async def test_log_mode_still_answers_with_telemetry_on(self, stack, telemetry, broken):
        app.state.audit_recorder = DatabaseAuditRecorder(broken, level="metadata", fail_mode="log")

        assert (await stack.post(ASK, json={"query": QUESTION})).status_code == 200

    async def test_a_trace_id_that_cannot_be_read_is_not_an_audit_write_failure(
        self, stack, telemetry, audit_factory, monkeypatch
    ):
        import src.services.audit.recorder as recorder

        def broken_trace_id():
            raise RuntimeError("no trace for you")

        monkeypatch.setattr(recorder, "current_trace_id", broken_trace_id)
        app.state.audit_recorder = DatabaseAuditRecorder(opener(audit_factory), level="metadata", fail_mode="deny")

        response = await stack.post(ASK, json={"query": QUESTION})

        assert response.status_code == 200
        assert len(query_events(audit_factory)) == 1


class TestLogLines:
    @pytest.fixture
    def json_lines(self):
        class Collect(logging.Handler):
            def __init__(self):
                super().__init__(logging.INFO)
                self.setFormatter(JsonFormatter())
                self.lines = []

            def emit(self, record):
                self.lines.append(self.format(record))

        handler = Collect()
        logging.getLogger().addHandler(handler)
        yield handler.lines
        logging.getLogger().removeHandler(handler)

    @pytest.fixture
    def noisy_route(self):
        def noisy():
            logging.getLogger("src.test.correlation").warning("something worth a line")
            return {}

        app.add_api_route("/__correlation/noisy", noisy)
        yield
        del app.router.routes[-1]

    def ours(self, lines):
        return [json.loads(line) for line in lines if "something worth a line" in line]

    @reads_telemetry
    async def test_a_line_logged_during_a_request_carries_its_request_id_and_trace_id(
        self, client, telemetry, noisy_route, json_lines
    ):
        response = await client.get("/__correlation/noisy", headers={"X-Request-ID": "follow-me-42"})

        (line,) = self.ours(json_lines)
        root = telemetry.span_named("GET /__correlation/noisy")
        assert response.headers["X-Request-ID"] == line["request_id"] == "follow-me-42"
        assert line["trace_id"] == hex_trace_id(root)
        assert line["message"] == "something worth a line" and line["level"] == "WARNING"

    async def test_without_telemetry_the_line_has_the_request_id_and_no_trace_id(self, client, noisy_route, json_lines):
        from src.services.telemetry import NOOP_TELEMETRY

        app.state.telemetry = NOOP_TELEMETRY
        await client.get("/__correlation/noisy", headers={"X-Request-ID": "follow-me-43"})

        (line,) = self.ours(json_lines)
        assert line["request_id"] == "follow-me-43"
        assert line["trace_id"] is None

    async def test_a_line_outside_any_request_has_neither(self, json_lines):
        logging.getLogger("src.test.correlation").warning("something worth a line")

        (line,) = self.ours(json_lines)
        assert line["request_id"] is None and line["trace_id"] is None

    async def test_an_exception_is_logged_as_json_too(self, json_lines):
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("src.test.correlation").exception("something worth a line")

        (line,) = self.ours(json_lines)
        assert "ValueError: boom" in line["exception"]
