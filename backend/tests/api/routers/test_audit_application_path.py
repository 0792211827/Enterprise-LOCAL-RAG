"""Audit events from the application answer path: the playground and /v1.

Offline against in-memory SQLite, like test_domain.py; the audit recorder
writes to the same database.
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.dependencies import (
    get_db_session,
    get_embeddings_service,
    get_llm_provider,
    get_opensearch_client,
    get_retrieval_scope,
)
from src.main import audit_write_failed
from src.routers import applications, chat_completions, knowledge_bases
from src.services.access import RetrievalScope
from src.services.audit import AuditWriteError, DatabaseAuditRecorder
from tests.audit_support import events, opener, sqlite_session_factory

HIT = {
    "chunk_id": "chunk-1",
    "chunk_text": "Leave is 25 days.",
    "title": "Handbook",
    "score": 0.9,
    "document_id": "d1",
    "knowledge_base_id": "kb-from-hit",
}


@pytest.fixture
def factory():
    return sqlite_session_factory()


@pytest.fixture
def llm():
    mock = AsyncMock()
    mock.generate_rag_answer = AsyncMock(return_value={"answer": "25 days.", "sources": []})

    async def _stream(**kwargs):
        for piece in ["25 ", "days."]:
            yield {"response": piece}

    mock.generate_rag_answer_stream = _stream
    return mock


@pytest.fixture
def opensearch():
    mock = MagicMock()
    mock.search_unified = MagicMock(return_value={"hits": [HIT]})
    return mock


@pytest.fixture
def app(factory, llm, opensearch):
    application = FastAPI()
    for module in (knowledge_bases, applications, chat_completions):
        application.include_router(module.router)
    application.add_exception_handler(AuditWriteError, audit_write_failed)

    def _session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 1024)
    application.dependency_overrides[get_db_session] = _session
    application.dependency_overrides[get_opensearch_client] = lambda: opensearch
    application.dependency_overrides[get_embeddings_service] = lambda: embeddings
    application.dependency_overrides[get_llm_provider] = lambda: llm
    application.state.audit_recorder = DatabaseAuditRecorder(opener(factory), level="full")
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _assistant(client, name="HR Bot", kb_count=1):
    kb_ids = [client.post("/api/v1/knowledge-bases", json={"name": f"{name} KB{i}"}).json()["id"] for i in range(kb_count)]
    return client.post("/api/v1/applications", json={"name": name, "knowledge_base_ids": kb_ids}).json(), kb_ids


def _queries(factory):
    return [e for e in events(factory) if e.action.startswith("query.")]


class TestPlayground:
    def test_records_one_answer_event(self, client, factory):
        assistant, kb_ids = _assistant(client)
        response = client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "How much leave?"})
        assert response.status_code == 200

        [event] = _queries(factory)
        assert event.action == "query.answer" and event.outcome == "ok"
        assert str(event.application_id) == assistant["id"]
        assert event.actor_subject == "api_user" and event.auth_method == "disabled"
        assert event.requested_kb_ids == kb_ids and event.permitted_kb_ids == kb_ids and event.dropped_kb_ids == []
        assert event.retrieved == [
            {"chunk_id": "chunk-1", "document_id": "d1", "knowledge_base_id": "kb-from-hit", "score": 0.9}
        ]
        assert (event.query_text, event.answer_text) == ("How much leave?", "25 days.")
        assert event.route == "/api/v1/applications/{application_id}/ask" and event.http_method == "POST"
        assert event.latency_ms is not None

    def test_records_knowledge_bases_dropped_by_grants(self, app, client, factory):
        assistant, (kb_a, kb_b) = _assistant(client, kb_count=2)
        app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope(kb_ids=frozenset({kb_a}), reason="grants")
        client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"})

        [event] = _queries(factory)
        assert event.permitted_kb_ids == [kb_a] and event.dropped_kb_ids == [kb_b]
        assert event.outcome == "ok"

    def test_caller_with_no_permitted_knowledge_base_is_denied(self, app, client, factory, llm):
        assistant, _ = _assistant(client)
        app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope(kb_ids=frozenset(), reason="grants")
        client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"})

        [event] = _queries(factory)
        assert event.outcome == "denied" and event.permitted_kb_ids == [] and event.retrieved == []
        llm.generate_rag_answer.assert_not_called()

    def test_generation_failure_is_recorded_as_error(self, client, factory, llm):
        assistant, _ = _assistant(client)
        llm.generate_rag_answer.side_effect = RuntimeError("model crashed")
        with pytest.raises(RuntimeError):
            client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"})

        [event] = _queries(factory)
        assert (event.outcome, event.error_code) == ("error", "RuntimeError")

    def test_unknown_application_records_no_query(self, client, factory):
        client.post("/api/v1/applications/00000000-0000-0000-0000-000000000000/ask", json={"query": "q"})
        assert _queries(factory) == []


class TestChatCompletions:
    def _ask(self, client, slug, stream=False, headers=None):
        body = {"model": slug, "messages": [{"role": "user", "content": "How much leave?"}], "stream": stream}
        return client.post("/v1/chat/completions", json=body, headers=headers or {})

    def test_records_the_application_key(self, client, factory):
        assistant, _ = _assistant(client)
        response = self._ask(client, assistant["slug"], headers={"Authorization": f"Bearer {assistant['api_key']}"})
        assert response.status_code == 200

        [event] = _queries(factory)
        assert event.auth_method == "api_key" and event.api_key_id is not None
        assert str(event.application_id) == assistant["id"]
        assert event.answer_text == "25 days." and event.retrieved[0]["chunk_id"] == "chunk-1"
        assert event.route == "/v1/chat/completions"

    def test_streaming_records_the_assembled_answer_once(self, client, factory):
        assistant, _ = _assistant(client)
        response = self._ask(client, assistant["slug"], stream=True)
        assert response.text.rstrip().endswith("data: [DONE]")

        [event] = _queries(factory)
        assert event.answer_text == "25 days." and event.outcome == "ok"
        assert event.retrieved[0]["chunk_id"] == "chunk-1"

    def test_streaming_generation_error_is_recorded(self, client, factory, llm):
        async def _broken(**kwargs):
            yield {"response": "25 "}
            raise RuntimeError("model crashed")

        llm.generate_rag_answer_stream = _broken
        assistant, _ = _assistant(client)
        self._ask(client, assistant["slug"], stream=True)

        [event] = _queries(factory)
        assert (event.outcome, event.error_code, event.answer_text) == ("error", "RuntimeError", "25 ")

    def test_request_rejected_before_answering_records_no_query(self, client, factory):
        self._ask(client, "no-such-model")
        assert _queries(factory) == []


class TestAgenticApplications:
    """rag_strategy=agentic runs the graph on both front doors, with the same scope and audit."""

    @pytest.fixture(autouse=True)
    def agent_backend(self, app):
        import httpx
        from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider
        from tests.unit.services.test_application_agentic import Backend

        self.backend = Backend()
        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(self.backend))
        app.dependency_overrides[get_llm_provider] = lambda: provider

    def _agentic(self, client, kb_count=1):
        kb_ids = [client.post("/api/v1/knowledge-bases", json={"name": f"Agentic KB{i}"}).json()["id"] for i in range(kb_count)]
        body = {"name": "Agent Bot", "knowledge_base_ids": kb_ids, "rag_strategy": "agentic"}
        return client.post("/api/v1/applications", json=body).json(), kb_ids

    def test_playground_runs_the_graph_and_audits_it_like_a_traditional_answer(self, client, factory, opensearch):
        assistant, kb_ids = self._agentic(client)
        response = client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "How much leave?"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"] == "Fourteen days, from the agent."
        assert body["chunks_used"] == 1 and body["sources"][0]["document_title"] == "Handbook"
        assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == kb_ids

        [event] = _queries(factory)
        assert event.action == "query.answer" and event.outcome == "ok"
        assert str(event.application_id) == assistant["id"]
        assert event.permitted_kb_ids == kb_ids and event.dropped_kb_ids == []
        assert event.retrieved == [
            {"chunk_id": "chunk-1", "document_id": "d1", "knowledge_base_id": "kb-from-hit", "score": 0.9}
        ]
        assert event.answer_text == "Fourteen days, from the agent." and event.details["agentic"] is True

    def test_completions_run_the_graph_streaming_or_not(self, client, factory):
        assistant, _ = self._agentic(client)
        body = {"model": assistant["slug"], "messages": [{"role": "user", "content": "How much leave?"}]}

        plain = client.post("/v1/chat/completions", json=body)
        assert plain.json()["choices"][0]["message"]["content"] == "Fourteen days, from the agent."

        streamed = client.post("/v1/chat/completions", json={**body, "stream": True})
        frames = [json.loads(f[len("data: ") :]) for f in streamed.text.splitlines() if f.startswith("data: ") and "[DONE]" not in f]
        assert "".join(f["choices"][0]["delta"].get("content", "") for f in frames) == "Fourteen days, from the agent."

        plain_event, streamed_event = _queries(factory)
        for event in (plain_event, streamed_event):
            assert event.answer_text == "Fourteen days, from the agent."
            assert event.retrieved[0]["chunk_id"] == "chunk-1" and event.details["agentic"] is True

    def test_a_caller_with_no_grants_is_refused_and_denied_on_both_paths(
        self, app, client, factory, opensearch, monkeypatch
    ):
        import src.routers.chat_completions as completions

        assistant, _ = self._agentic(client)
        no_grants = RetrievalScope(kb_ids=frozenset(), reason="grants")
        app.dependency_overrides[get_retrieval_scope] = lambda: no_grants
        monkeypatch.setattr(completions, "resolve_scope", lambda *args: no_grants)  # /v1 resolves its own scope

        playground = client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"}).json()
        assert playground["chunks_used"] == 0 and playground["sources"] == []
        assert "don't have access" in playground["answer"]

        completion = client.post(
            "/v1/chat/completions", json={"model": assistant["slug"], "messages": [{"role": "user", "content": "q"}]}
        )
        assert "don't have access" in completion.json()["choices"][0]["message"]["content"]

        assert {e.outcome for e in _queries(factory)} == {"denied"}
        assert self.backend.requests == []
        opensearch.search_unified.assert_not_called()


class TestRerank:
    """The application path reranks through the shared seam, on both front doors."""

    @pytest.fixture
    def with_reranker(self, app, opensearch):
        from src.dependencies import get_reranker
        from tests.unit.services.test_reranker import reranker_for

        opensearch.search_unified = MagicMock(
            side_effect=lambda **kw: {
                "hits": [dict(HIT, chunk_id=f"c-{t}", chunk_text=t, title=t) for t in ["alpha", "bravo", "charlie", "delta", "echo"]][: kw["size"]]
            }
        )

        def _use(endpoint):
            reranker = reranker_for(endpoint)
            app.dependency_overrides[get_reranker] = lambda: reranker

        return _use

    def test_the_playground_answers_from_the_reranked_passages(self, client, factory, llm, with_reranker):
        from tests.unit.services.test_reranker import FakeEndpoint

        with_reranker(FakeEndpoint())
        assistant, _ = _assistant(client)
        response = client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q", "top_k": 2})

        assert [s["document_title"] for s in response.json()["sources"]] == ["echo", "charlie"]
        assert [c["chunk_text"] for c in llm.generate_rag_answer.call_args.kwargs["chunks"]] == ["echo", "charlie"]
        assert _queries(factory)[0].details == {"reranked": True}

    def test_completions_flag_a_degraded_rerank_and_still_answer(self, client, factory, llm, with_reranker):
        import httpx
        from tests.unit.services.test_reranker import FakeEndpoint

        with_reranker(FakeEndpoint(raises=httpx.ReadTimeout("slow")))
        assistant, _ = _assistant(client)
        body = {"model": assistant["slug"], "messages": [{"role": "user", "content": "q"}]}

        for stream in (False, True):
            response = client.post("/v1/chat/completions", json={**body, "stream": stream})
            assert response.status_code == 200
        assert [e.details for e in _queries(factory)] == [{"rerank_skipped": True}] * 2
        assert all(e.outcome == "ok" for e in _queries(factory))


class TestDenyMode:
    @pytest.fixture
    def failing(self, app):
        @contextmanager
        def broken():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        app.state.audit_recorder = DatabaseAuditRecorder(broken, level="metadata", fail_mode="deny")

    def test_playground_refuses_with_503(self, client, failing):
        assistant, _ = _assistant(client)
        response = client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"})
        assert response.status_code == 503
        assert "audit log is unavailable" in response.json()["detail"]

    def test_chat_completions_refuses_in_openai_shape(self, client, failing):
        assistant, _ = _assistant(client)
        response = TestChatCompletions()._ask(client, assistant["slug"])
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "audit_unavailable"

    def test_stream_ends_with_an_error_event_before_done(self, client, failing):
        assistant, _ = _assistant(client)
        response = TestChatCompletions()._ask(client, assistant["slug"], stream=True)
        frames = [line[len("data: ") :] for line in response.text.splitlines() if line.startswith("data: ")]
        assert frames[-1] == "[DONE]"
        assert json.loads(frames[-2])["error"]["code"] == "audit_unavailable"

    def test_log_mode_still_answers(self, app, client):
        @contextmanager
        def broken():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        app.state.audit_recorder = DatabaseAuditRecorder(broken, level="metadata", fail_mode="log")
        assistant, _ = _assistant(client)
        assert client.post(f"/api/v1/applications/{assistant['id']}/ask", json={"query": "q"}).status_code == 200
