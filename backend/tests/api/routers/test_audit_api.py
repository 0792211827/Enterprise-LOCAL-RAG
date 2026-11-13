"""The auditor API: listing, filters, pagination, detail, export and status."""

import csv
import io
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.dependencies import get_db_session
from src.main import audit_write_failed
from src.models import AuditEvent
from src.routers import audit
from src.services.audit import AuditWriteError, DatabaseAuditRecorder
from tests.audit_support import events, opener, sqlite_session_factory

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
APP_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
KB_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def factory():
    return sqlite_session_factory()


@pytest.fixture
def client(factory):
    app = FastAPI()
    app.include_router(audit.router)
    app.add_exception_handler(AuditWriteError, audit_write_failed)

    def _session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _session
    app.state.audit_recorder = DatabaseAuditRecorder(opener(factory), level="metadata")
    return TestClient(app)


def _seed(factory, **fields):
    values = dict(action="query.answer", outcome="ok", occurred_at=T0, actor_subject="alice")
    values.update(fields)
    with factory() as session:
        event = AuditEvent(**values)
        session.add(event)
        session.commit()
        return event.id


class TestList:
    def test_newest_first_and_serialised_in_utc(self, client, factory):
        _seed(factory, occurred_at=T0)
        newest = _seed(factory, occurred_at=T0 + timedelta(minutes=5))
        body = client.get("/api/v1/audit/events").json()
        assert [item["id"] for item in body["items"]][0] == str(newest)
        assert body["items"][0]["occurred_at"].endswith(("+00:00", "Z"))
        assert body["next_cursor"] is None

    def test_pagination_visits_every_event_once(self, client, factory):
        # Two share a timestamp: the id tie-break must still page them apart.
        ids = {_seed(factory, occurred_at=T0 + timedelta(seconds=i // 2)) for i in range(5)}
        seen, cursor = [], None
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = client.get("/api/v1/audit/events", params=params).json()
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert sorted(seen) == sorted(str(i) for i in ids) and len(seen) == 5

    def test_invalid_cursor(self, client):
        assert client.get("/api/v1/audit/events", params={"cursor": "garbage!"}).status_code == 400

    def test_limit_is_bounded(self, client):
        assert client.get("/api/v1/audit/events", params={"limit": 501}).status_code == 422

    def test_unknown_action_is_rejected(self, client):
        assert client.get("/api/v1/audit/events", params={"action": "query.delete"}).status_code == 422


class TestFilters:
    @pytest.fixture
    def seeded(self, factory):
        return {
            "bob": _seed(factory, actor_subject="bob"),
            "search": _seed(factory, action="query.search"),
            "denied": _seed(factory, outcome="denied", dropped_kb_ids=[str(KB_ID)], permitted_kb_ids=[]),
            "app": _seed(factory, application_id=APP_ID),
            "retrieved": _seed(factory, retrieved=[{"chunk_id": "c", "knowledge_base_id": str(KB_ID)}]),
            "old": _seed(factory, occurred_at=T0 - timedelta(days=30)),
            "rid": _seed(factory, request_id="req-9"),
        }

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({"actor": "bob"}, {"bob"}),
            ({"action": "query.search"}, {"search"}),
            ({"outcome": "denied"}, {"denied"}),
            ({"application_id": str(APP_ID)}, {"app"}),
            ({"knowledge_base_id": str(KB_ID)}, {"denied", "retrieved"}),
            ({"until": (T0 - timedelta(days=1)).isoformat()}, {"old"}),
            ({"request_id": "req-9"}, {"rid"}),
        ],
    )
    def test_filter(self, client, seeded, params, expected):
        items = client.get("/api/v1/audit/events", params=params).json()["items"]
        names = {name for name, event_id in seeded.items() if str(event_id) in {i["id"] for i in items}}
        assert names == expected

    def test_since_is_inclusive_and_naive_means_utc(self, client, seeded):
        items = client.get("/api/v1/audit/events", params={"since": "2026-09-01T12:00:00"}).json()["items"]
        assert str(seeded["old"]) not in {i["id"] for i in items} and len(items) == 6


class TestDetail:
    def test_found(self, client, factory):
        event_id = _seed(factory, details={"k": "v"})
        body = client.get(f"/api/v1/audit/events/{event_id}").json()
        assert body["details"] == {"k": "v"} and body["actor_subject"] == "alice"

    def test_missing(self, client):
        assert client.get(f"/api/v1/audit/events/{uuid.uuid4()}").status_code == 404

    def test_there_is_no_way_to_change_events(self, client, factory):
        event_id = _seed(factory)
        for method in ("put", "patch", "delete"):
            assert getattr(client, method)(f"/api/v1/audit/events/{event_id}").status_code == 405


class TestExport:
    def test_ndjson_streams_every_match_and_is_itself_audited(self, client, factory, monkeypatch):
        monkeypatch.setattr(audit, "EXPORT_BATCH", 2)  # force several batches
        for i in range(5):
            _seed(factory, occurred_at=T0 + timedelta(seconds=i), actor_subject="bob")
        response = client.get("/api/v1/audit/export", params={"actor": "bob"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert "attachment" in response.headers["content-disposition"]
        lines = [json.loads(line) for line in response.text.splitlines()]
        assert len(lines) == 5 and all(line["actor_subject"] == "bob" for line in lines)

        [export] = [e for e in events(factory) if e.action == "audit.export"]
        assert export.details == {"format": "ndjson", "filters": {"actor": "bob"}}
        assert export.route == "/api/v1/audit/export"

    def test_csv_has_a_header_and_neutralises_formulas(self, client, factory):
        _seed(factory, actor_subject="=HYPERLINK(\"http://x\")", actor_groups=["a"])
        rows = list(csv.reader(io.StringIO(client.get("/api/v1/audit/export", params={"format": "csv"}).text)))
        header, *data = rows
        assert header[:3] == ["id", "occurred_at", "request_id"]
        row = dict(zip(header, next(r for r in data if r[header.index("action")] == "query.answer")))
        assert row["actor_subject"].startswith("'=")
        assert row["actor_groups"] == '["a"]'

    def test_csv_with_no_matches_is_just_the_header(self, client):
        text = client.get("/api/v1/audit/export", params={"format": "csv", "actor": "nobody"}).text
        assert text.strip().splitlines()[0].startswith("id,occurred_at")
        assert len(text.strip().splitlines()) == 1

    def test_export_that_cannot_be_audited_is_refused_under_deny(self, client):
        @contextmanager
        def broken():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        client.app.state.audit_recorder = DatabaseAuditRecorder(broken, fail_mode="deny")
        assert client.get("/api/v1/audit/export").status_code == 503


def test_status_reports_configuration(client):
    assert client.get("/api/v1/audit/status").json() == {"level": "off", "fail_mode": "log", "retention_days": 0}

