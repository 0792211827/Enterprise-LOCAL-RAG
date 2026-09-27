"""The audit recorder: level filtering, failure policy, and the factory."""

import hashlib
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from src.auth import Principal, Role
from src.config import AuditSettings, Settings
from src.services.access import ScopeDecision
from src.services.audit import (
    AuditContext,
    AuditRecord,
    AuditWriteError,
    DatabaseAuditRecorder,
    NoopAuditRecorder,
    decision_outcome,
    make_audit_recorder,
    principal_fields,
    record_query_event,
    retrieved_from_hits,
)
from tests.audit_support import events, opener, sqlite_session_factory


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def factory():
    return sqlite_session_factory()


def _record(**overrides) -> AuditRecord:
    values = dict(action="query.answer", outcome="ok", query_text="What is RAG?", answer_text="Retrieval.")
    values.update(overrides)
    return AuditRecord(**values)


class TestLevels:
    def test_metadata_stores_digests_but_no_text(self, factory):
        DatabaseAuditRecorder(opener(factory), level="metadata").record(_record())
        [row] = events(factory)
        assert row.query_text is None and row.answer_text is None
        assert row.query_sha256 == _sha("What is RAG?")
        assert row.answer_sha256 == _sha("Retrieval.")

    def test_full_stores_text_and_digests(self, factory):
        DatabaseAuditRecorder(opener(factory), level="full").record(_record())
        [row] = events(factory)
        assert (row.query_text, row.answer_text) == ("What is RAG?", "Retrieval.")
        assert row.query_sha256 == _sha("What is RAG?")

    def test_missing_answer_has_no_digest(self, factory):
        DatabaseAuditRecorder(opener(factory), level="full").record(_record(answer_text=None))
        assert events(factory)[0].answer_sha256 is None

    def test_off_is_rejected_for_the_database_recorder(self, factory):
        with pytest.raises(ValueError):
            DatabaseAuditRecorder(opener(factory), level="off")

    def test_noop_writes_nothing(self, factory):
        recorder = NoopAuditRecorder()
        assert recorder.enabled is False
        recorder.record(_record())
        assert events(factory) == []

    def test_json_and_server_fields_round_trip(self, factory):
        DatabaseAuditRecorder(opener(factory)).record(
            _record(permitted_kb_ids=["kb-a"], dropped_kb_ids=[], retrieved=[{"chunk_id": "c1"}], details={"k": 1})
        )
        [row] = events(factory)
        assert row.id is not None and row.occurred_at is not None
        assert row.permitted_kb_ids == ["kb-a"] and row.dropped_kb_ids == []
        assert row.retrieved == [{"chunk_id": "c1"}] and row.details == {"k": 1}


class TestFailureModes:
    @staticmethod
    def _broken():
        @contextmanager
        def open_session():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        return open_session

    def test_log_mode_swallows_the_failure(self, caplog):
        DatabaseAuditRecorder(self._broken(), fail_mode="log").record(_record())
        assert "Failed to write audit event" in caplog.text

    def test_deny_mode_raises(self):
        with pytest.raises(AuditWriteError):
            DatabaseAuditRecorder(self._broken(), fail_mode="deny").record(_record())

    def test_writes_use_their_own_session(self, factory):
        opened = []
        inner = opener(factory)

        @contextmanager
        def tracking():
            with inner() as session:
                opened.append(session)
                yield session

        recorder = DatabaseAuditRecorder(tracking)
        recorder.record(_record())
        recorder.record(_record())
        assert len(opened) == 2 and opened[0] is not opened[1]


class TestFactory:
    def test_default_is_off(self):
        assert AuditSettings().level == "off"
        assert isinstance(make_audit_recorder(Settings(), MagicMock()), NoopAuditRecorder)

    def test_off_never_touches_the_database(self):
        database = MagicMock()
        make_audit_recorder(Settings(), database).record(_record())
        database.get_session.assert_not_called()

    def test_enabled_levels_build_a_database_recorder(self):
        settings = Settings(audit=AuditSettings(level="full", fail_mode="deny"))
        database = MagicMock()
        recorder = make_audit_recorder(settings, database)
        assert isinstance(recorder, DatabaseAuditRecorder)
        assert (recorder.level, recorder.fail_mode) == ("full", "deny")
        assert recorder.open_session == database.get_session

    def test_retention_cannot_be_negative(self):
        with pytest.raises(ValueError):
            AuditSettings(retention_days=-1)


class TestQueryEvents:
    def test_decision_outcome(self):
        assert decision_outcome(None) == "ok"
        assert decision_outcome(ScopeDecision(permitted=None)) == "ok"
        # A global search by a caller who may read nothing.
        assert decision_outcome(ScopeDecision(permitted=[])) == "denied"
        # Every application knowledge base was dropped.
        assert decision_outcome(ScopeDecision(permitted=[], dropped=["kb"], requested=["kb"])) == "denied"
        # An application with nothing attached is a configuration gap, not a denial.
        assert decision_outcome(ScopeDecision(permitted=[], requested=[])) == "ok"
        assert decision_outcome(ScopeDecision(permitted=["a"], dropped=["b"], requested=["a", "b"])) == "ok"

    def test_retrieved_keeps_identifiers_not_text(self):
        hits = [{"chunk_id": "c1", "document_id": "d1", "knowledge_base_id": "k1", "score": 0.5, "chunk_text": "secret"}]
        assert retrieved_from_hits(hits) == [
            {"chunk_id": "c1", "document_id": "d1", "knowledge_base_id": "k1", "score": 0.5}
        ]
        assert retrieved_from_hits(None) == []

    def test_principal_fields(self):
        principal = Principal(
            subject="alice", roles=frozenset({Role.QUERIER}), auth_method="jwt", groups=frozenset({"b", "a"})
        )
        assert principal_fields(principal)["actor_groups"] == ["a", "b"]
        assert principal_fields(principal)["actor_roles"] == ["querier"]
        assert principal_fields(None) == {}

    def test_record_query_event_maps_the_decision(self, factory):
        recorder = DatabaseAuditRecorder(opener(factory), level="full")
        context = AuditContext(request_id="rid-1", actor_subject="alice", route="/api/v1/ask", http_method="POST")
        record_query_event(
            recorder,
            context,
            action="query.answer",
            query="q",
            answer="a",
            decision=ScopeDecision(permitted=["kb-a"], dropped=["kb-b"], requested=["kb-a", "kb-b"]),
            hits=[{"chunk_id": "c1", "document_id": "d1", "knowledge_base_id": "kb-a", "score": 1.0}],
            model="m",
            latency_ms=12,
        )
        [row] = events(factory)
        assert (row.request_id, row.actor_subject, row.route, row.outcome) == ("rid-1", "alice", "/api/v1/ask", "ok")
        assert row.requested_kb_ids == ["kb-a", "kb-b"]
        assert row.permitted_kb_ids == ["kb-a"] and row.dropped_kb_ids == ["kb-b"]
        assert row.retrieved[0]["chunk_id"] == "c1"
        assert (row.model, row.latency_ms) == ("m", 12)

    def test_disabled_recorder_skips_work(self):
        recorder = MagicMock(enabled=False)
        record_query_event(recorder, AuditContext(), action="query.answer", query="q")
        recorder.record.assert_not_called()
