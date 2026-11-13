"""Reranking on the legacy paths: reordered when the endpoint works, original order and an audit flag when it does not."""

import httpx
import pytest
import src.main as main
from src.services.audit import DatabaseAuditRecorder
from tests.audit_support import events, opener, sqlite_session_factory
from tests.unit.services.test_reranker import FakeEndpoint, hits_for, reranker_for


def _hit(text):
    return {
        "chunk_id": f"c-{text}",
        "chunk_text": text,
        "title": text.title(),
        "document_id": f"d-{text}",
        "knowledge_base_id": "kb-a",
        "score": 0.5,
    }


@pytest.fixture
def audit_factory(client, monkeypatch):
    factory = sqlite_session_factory()
    monkeypatch.setattr(main.app.state, "audit_recorder", DatabaseAuditRecorder(opener(factory), level="metadata"))
    index = main.app.state.opensearch_client
    index.search_unified.side_effect = lambda **kw: {"total": 5, "hits": [_hit(t) for t in ["alpha", "bravo", "charlie", "delta", "echo"]][: kw["size"]]}
    return factory


@pytest.fixture
def use_endpoint(client, monkeypatch):
    def _use(endpoint):
        monkeypatch.setattr(main.app.state, "reranker", reranker_for(endpoint))
        return endpoint

    return _use


def _last_event(factory):
    return events(factory)[-1]


class TestHybridSearch:
    async def test_hits_come_back_reranked_and_the_audit_says_so(self, client, audit_factory, use_endpoint):
        use_endpoint(FakeEndpoint())
        body = (await client.post("/api/v1/hybrid-search/", json={"query": "q", "size": 2})).json()

        assert [h["chunk_text"] for h in body["hits"]] == ["echo", "charlie"]
        assert _last_event(audit_factory).details == {"reranked": True}

    async def test_a_down_endpoint_keeps_the_order_and_is_flagged(self, client, audit_factory, use_endpoint):
        use_endpoint(FakeEndpoint(raises=httpx.ConnectError("down")))
        response = await client.post("/api/v1/hybrid-search/", json={"query": "q", "size": 2})

        assert response.status_code == 200
        assert [h["chunk_text"] for h in response.json()["hits"]] == ["alpha", "bravo"]
        assert _last_event(audit_factory).details == {"rerank_skipped": True}

    async def test_disabled_leaves_no_trace(self, client, audit_factory):
        response = await client.post("/api/v1/hybrid-search/", json={"query": "q", "size": 2})

        assert [h["chunk_text"] for h in response.json()["hits"]] == ["alpha", "bravo"]
        assert _last_event(audit_factory).details is None


class TestAsk:
    async def test_the_model_is_given_the_reranked_passages(self, client, audit_factory, use_endpoint):
        use_endpoint(FakeEndpoint())
        response = await client.post("/api/v1/ask", json={"query": "q", "top_k": 2})

        assert response.status_code == 200
        chunks = main.app.state.ollama_client.generate_rag_answer.call_args.kwargs["chunks"]
        assert [c["chunk_text"] for c in chunks] == ["echo", "charlie"]
        assert _last_event(audit_factory).details == {"reranked": True}

    async def test_a_slow_endpoint_still_answers_in_the_original_order(self, client, audit_factory, use_endpoint):
        use_endpoint(FakeEndpoint(raises=httpx.ReadTimeout("slow")))
        response = await client.post("/api/v1/ask", json={"query": "q", "top_k": 2})

        assert response.status_code == 200
        chunks = main.app.state.ollama_client.generate_rag_answer.call_args.kwargs["chunks"]
        assert [c["chunk_text"] for c in chunks] == ["alpha", "bravo"]
        assert _last_event(audit_factory).details == {"rerank_skipped": True}


class TestStream:
    async def test_streamed_answers_use_the_reranked_passages(self, client, audit_factory, use_endpoint):
        use_endpoint(FakeEndpoint())
        given = []

        async def _stream(**kwargs):
            given.append(kwargs["chunks"])
            yield {"response": "ok", "done": True}

        main.app.state.ollama_client.generate_rag_answer_stream = _stream
        response = await client.post("/api/v1/stream", json={"query": "q", "top_k": 2})

        assert response.status_code == 200
        assert [c["chunk_text"] for c in given[0]] == ["echo", "charlie"]
        assert _last_event(audit_factory).details == {"reranked": True}


def test_the_agentic_service_receives_the_reranker():
    from unittest.mock import MagicMock

    from src.config import Settings
    from src.dependencies import get_agentic_rag_service

    reranker = reranker_for(FakeEndpoint())
    service = get_agentic_rag_service(MagicMock(), MagicMock(), MagicMock(), MagicMock(), Settings(), None, None, reranker)
    assert service.reranker is reranker


async def test_the_agentic_retriever_reranks_and_reports_a_skip():
    from unittest.mock import AsyncMock, MagicMock

    from src.services.agents.tools import RetrievalLog, create_retriever_tool, retrieval_log

    opensearch = MagicMock()
    opensearch.search_unified = MagicMock(side_effect=lambda **kw: {"total": 5, "hits": hits_for()[: kw["size"]]})
    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1])

    log = RetrievalLog()
    token = retrieval_log.set(log)
    try:
        ok = create_retriever_tool(opensearch, embeddings, top_k=2, reranker=reranker_for(FakeEndpoint()))
        assert [d.page_content for d in await ok.ainvoke({"query": "q"})] == ["echo", "charlie"]
        assert log.rerank == "applied"

        down = create_retriever_tool(opensearch, embeddings, top_k=2, reranker=reranker_for(FakeEndpoint(status=500, body={})))
        assert [d.page_content for d in await down.ainvoke({"query": "q"})] == ["alpha", "bravo"]
        assert log.rerank == "skipped"
    finally:
        retrieval_log.reset(token)
