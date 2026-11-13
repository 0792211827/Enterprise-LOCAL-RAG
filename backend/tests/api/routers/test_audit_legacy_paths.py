"""Audit events from the legacy global endpoints, on the real application:
/ask, /stream, /ask-agentic and /hybrid-search."""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import src.main as main
from src.auth import Principal, Role, get_principal
from src.dependencies import get_agentic_rag_service, get_retrieval_scope
from src.schemas.api.ask import AskResponse
from src.services.access import RetrievalScope
from src.services.audit import DatabaseAuditRecorder
from tests.audit_support import events, opener, sqlite_session_factory

HITS = {
    "total": 1,
    "hits": [
        {
            "chunk_id": "chunk-1",
            "chunk_text": "Neural networks are function approximators.",
            "title": "Deep Learning",
            "document_id": "doc-1",
            "knowledge_base_id": "kb-a",
            "score": 0.9,
            "section_title": "Intro",
        }
    ],
}


@pytest.fixture
def factory(client, monkeypatch):
    """Record into SQLite. Depends on ``client`` so the lifespan has run first."""
    session_factory = sqlite_session_factory()
    monkeypatch.setattr(main.app.state, "audit_recorder", DatabaseAuditRecorder(opener(session_factory), level="full"))
    main.app.state.opensearch_client.search_unified.return_value = HITS
    return session_factory


@pytest.fixture
def overrides():
    yield main.app.dependency_overrides
    for dependency in (get_retrieval_scope, get_principal, get_agentic_rag_service):
        main.app.dependency_overrides.pop(dependency, None)


def _scope(*kb_ids):
    return lambda: RetrievalScope(kb_ids=frozenset(kb_ids), reason="grants")


class TestAsk:
    async def test_records_the_caller_the_scope_and_the_chunks(self, client, factory, overrides):
        overrides[get_principal] = lambda: Principal(
            subject="alice", roles=frozenset({Role.QUERIER}), auth_method="jwt", groups=frozenset({"finance"})
        )
        overrides[get_retrieval_scope] = _scope("kb-a", "kb-b")
        response = await client.post(
            "/api/v1/ask", json={"query": "What are neural networks?"}, headers={"X-Request-ID": "req-42"}
        )
        assert response.status_code == 200

        [event] = events(factory)
        assert (event.action, event.outcome, event.request_id) == ("query.answer", "ok", "req-42")
        assert (event.actor_subject, event.actor_groups, event.actor_roles) == ("alice", ["finance"], ["querier"])
        assert event.requested_kb_ids is None  # a global search...
        assert event.permitted_kb_ids == ["kb-a", "kb-b"]  # ...narrowed to the caller's grants
        assert event.retrieved == [{"chunk_id": "chunk-1", "document_id": "doc-1", "knowledge_base_id": "kb-a", "score": 0.9}]
        assert (event.answer_text, event.model) == ("Mock answer", "llama3.2:1b")
        assert event.source_ip is not None and event.route == "/api/v1/ask"

    async def test_unrestricted_scope_is_recorded_as_null(self, client, factory):
        await client.post("/api/v1/ask", json={"query": "q"})
        [event] = events(factory)
        assert event.permitted_kb_ids is None and event.actor_subject == "api_user"

    async def test_empty_scope_is_denied_and_retrieves_nothing(self, client, factory, overrides):
        overrides[get_retrieval_scope] = _scope()
        await client.post("/api/v1/ask", json={"query": "q"})
        [event] = events(factory)
        assert (event.outcome, event.permitted_kb_ids, event.retrieved) == ("denied", [], [])

    async def test_cache_hit_records_the_cited_sources(self, client, factory, monkeypatch):
        cache = MagicMock()
        cached = AskResponse(query="q", answer="cached answer", sources=["Deep Learning"], chunks_used=1, search_mode="hybrid")
        cache.find_cached_response = AsyncMock(return_value=cached)
        monkeypatch.setattr(main.app.state, "cache_client", cache)
        await client.post("/api/v1/ask", json={"query": "q"})

        [event] = events(factory)
        assert event.cache_hit is True and event.answer_text == "cached answer"
        assert event.retrieved is None  # no retrieval ran for this request
        assert event.details == {"cached_sources": ["Deep Learning"]}

    async def test_failure_is_recorded(self, client, factory):
        main.app.state.ollama_client.generate_rag_answer.side_effect = RuntimeError("model crashed")
        try:
            response = await client.post("/api/v1/ask", json={"query": "q"})
        finally:
            main.app.state.ollama_client.generate_rag_answer.side_effect = None
        assert response.status_code == 500
        [event] = events(factory)
        assert (event.outcome, event.error_code) == ("error", "http_500")


class TestStream:
    @staticmethod
    def _stream_tokens(*pieces):
        async def _stream(**kwargs):
            for piece in pieces:
                yield {"response": piece}
            yield {"done": True}

        return _stream

    async def test_records_the_streamed_answer(self, client, factory):
        main.app.state.ollama_client.generate_rag_answer_stream = self._stream_tokens("Function ", "approximators.")
        response = await client.post("/api/v1/stream", json={"query": "q"})
        assert '"done": true' in response.text

        [event] = events(factory)
        assert event.answer_text == "Function approximators."
        assert event.retrieved[0]["chunk_id"] == "chunk-1" and event.outcome == "ok"

    async def test_empty_scope(self, client, factory, overrides):
        overrides[get_retrieval_scope] = _scope()
        await client.post("/api/v1/stream", json={"query": "q"})
        [event] = events(factory)
        assert event.outcome == "denied" and event.answer_text == "No relevant information found."

    async def test_stream_error_is_recorded(self, client, factory):
        async def _broken(**kwargs):
            raise RuntimeError("model crashed")
            yield  # pragma: no cover

        main.app.state.ollama_client.generate_rag_answer_stream = _broken
        response = await client.post("/api/v1/stream", json={"query": "q"})
        assert "model crashed" in response.text
        [event] = events(factory)
        assert (event.outcome, event.error_code) == ("error", "RuntimeError")

    async def test_deny_mode_appends_an_error_frame(self, client, factory, monkeypatch):
        @contextmanager
        def broken():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        monkeypatch.setattr(main.app.state, "audit_recorder", DatabaseAuditRecorder(broken, fail_mode="deny"))
        main.app.state.ollama_client.generate_rag_answer_stream = self._stream_tokens("ok")
        response = await client.post("/api/v1/stream", json={"query": "q"})
        last = [line for line in response.text.splitlines() if line.startswith("data: ")][-1]
        assert "audit log is unavailable" in json.loads(last[len("data: ") :])["error"]


class TestAgentic:
    @pytest.fixture
    def agent(self, overrides):
        service = MagicMock()
        service.ask = AsyncMock(
            return_value={"query": "q", "answer": "Agent answer", "sources": ["Handbook"], "retrieval_attempts": 2}
        )
        overrides[get_agentic_rag_service] = lambda: service
        return service

    async def test_records_the_answer_and_attempts(self, client, factory, agent):
        assert (await client.post("/api/v1/ask-agentic", json={"query": "q"})).status_code == 200
        [event] = events(factory)
        assert event.answer_text == "Agent answer"
        assert event.details == {"agentic": True, "retrieval_attempts": 2, "cited_sources": ["Handbook"]}

    async def test_refusal_is_denied(self, client, factory, agent, overrides):
        overrides[get_retrieval_scope] = _scope()
        await client.post("/api/v1/ask-agentic", json={"query": "q"})
        [event] = events(factory)
        assert event.outcome == "denied" and event.retrieved is None
        agent.ask.assert_not_called()

    async def test_invalid_query_is_an_error(self, client, factory, agent):
        agent.ask.side_effect = ValueError("empty")
        assert (await client.post("/api/v1/ask-agentic", json={"query": "q"})).status_code == 422
        assert events(factory)[0].error_code == "http_422"


class TestHybridSearch:
    async def test_records_a_search_without_an_answer(self, client, factory, overrides):
        overrides[get_retrieval_scope] = _scope("kb-a")
        response = await client.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False})
        assert response.status_code == 200

        [event] = events(factory)
        assert (event.action, event.answer_text, event.answer_sha256) == ("query.search", None, None)
        assert event.permitted_kb_ids == ["kb-a"]
        assert event.retrieved[0] == {"chunk_id": "chunk-1", "document_id": "doc-1", "knowledge_base_id": "kb-a", "score": 0.9}


class TestOff:
    async def test_default_recorder_writes_nothing(self, client):
        # The lifespan (pinned by conftest) installed the no-op recorder.
        assert main.app.state.audit_recorder.enabled is False
        assert (await client.post("/api/v1/ask", json={"query": "q"})).status_code == 200
