"""The application answer path honours the caller's retrieval scope."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.access import RetrievalScope
from src.services.applications import (
    NO_KNOWLEDGE_BASE_MESSAGE,
    NO_PERMITTED_KNOWLEDGE_MESSAGE,
    answer_for_application,
    retrieve_for_application,
    stream_for_application,
)


def make_app(kb_ids=("k1", "k2", "k3"), mode="hybrid"):
    return SimpleNamespace(
        slug="bot",
        system_prompt=None,
        knowledge_bases=[SimpleNamespace(id=k) for k in kb_ids],
        retrieval_configuration=SimpleNamespace(mode=mode, top_k=8, score_threshold=None),
        model_configuration=SimpleNamespace(llm_model="qwen2.5:7b"),
    )


def scope(*kb_ids) -> RetrievalScope:
    return RetrievalScope(kb_ids=frozenset(kb_ids), reason="grants")


@pytest.fixture
def collaborators():
    opensearch = MagicMock()
    opensearch.search_unified = MagicMock(return_value={"hits": [{"chunk_text": "t", "title": "T", "score": 1.0}]})
    opensearch.search_chunks_vector = MagicMock(return_value={"hits": [{"chunk_text": "t", "title": "T"}]})
    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 8)
    llm = AsyncMock()
    llm.generate_rag_answer = AsyncMock(return_value={"answer": "ok"})
    return opensearch, embeddings, llm


@pytest.mark.parametrize("mode, method", [("hybrid", "search_unified"), ("bm25", "search_unified"), ("vector", "search_chunks_vector")])
async def test_search_is_limited_to_permitted_knowledge_bases(collaborators, mode, method):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(make_app(mode=mode), "q", opensearch, embeddings, llm, scope=scope("k2", "other"))
    assert getattr(opensearch, method).call_args.kwargs["knowledge_base_ids"] == ["k2"]


async def test_decision_reports_dropped_knowledge_bases(collaborators):
    opensearch, embeddings, llm = collaborators
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, llm, scope=scope("k2"))
    assert result.scope_decision.permitted == ["k2"]
    assert result.scope_decision.dropped == ["k1", "k3"]


async def test_nothing_permitted_fails_closed(collaborators):
    opensearch, embeddings, llm = collaborators
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, llm, scope=scope())

    assert result.answer == NO_PERMITTED_KNOWLEDGE_MESSAGE
    assert result.chunks == [] and result.hits == []
    opensearch.search_unified.assert_not_called()
    opensearch.search_chunks_vector.assert_not_called()
    embeddings.embed_query.assert_not_called()
    llm.generate_rag_answer.assert_not_called()


async def test_no_knowledge_bases_keeps_its_own_message(collaborators):
    opensearch, embeddings, llm = collaborators
    result = await answer_for_application(make_app(kb_ids=[]), "q", opensearch, embeddings, llm, scope=scope("k1"))
    assert result.answer == NO_KNOWLEDGE_BASE_MESSAGE


async def test_no_scope_behaves_as_before(collaborators):
    opensearch, embeddings, llm = collaborators
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, llm)
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k1", "k2", "k3"]
    assert result.scope_decision.dropped == []


async def test_unrestricted_scope_behaves_as_before(collaborators):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(
        make_app(), "q", opensearch, embeddings, llm, scope=RetrievalScope.unrestricted("auth disabled")
    )
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k1", "k2", "k3"]


async def test_retrieve_signals_blocked_with_none(collaborators):
    opensearch, embeddings, _ = collaborators
    chunks, hits, _, _ = await retrieve_for_application(make_app(), "q", opensearch, embeddings, scope=scope())
    assert chunks is None and hits == []


async def test_stream_yields_blocked_message(collaborators):
    opensearch, embeddings, llm = collaborators
    deltas = [d async for d in stream_for_application(make_app(), "q", opensearch, embeddings, llm, scope=scope())]
    assert deltas == [NO_PERMITTED_KNOWLEDGE_MESSAGE]
