"""The one retrieval function every answer and search path goes through."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.access import RetrievalScope
from src.services.retrieval import retrieve

HIT = {"chunk_text": "t", "title": "T", "document_id": "d1", "score": 0.5}


@pytest.fixture
def opensearch():
    client = MagicMock()
    client.search_unified = MagicMock(return_value={"total": 7, "hits": [dict(HIT)]})
    client.search_chunks_vector = MagicMock(return_value={"total": 3, "hits": [dict(HIT)]})
    return client


@pytest.fixture
def embeddings():
    client = AsyncMock()
    client.embed_query = AsyncMock(return_value=[0.1, 0.2])
    return client


def scope(*kb_ids) -> RetrievalScope:
    return RetrievalScope(kb_ids=frozenset(kb_ids), reason="grants")


async def test_requested_knowledge_bases_are_intersected_with_the_scope(opensearch, embeddings):
    result = await retrieve(opensearch, embeddings, "q", scope=scope("k2", "k9"), requested_kb_ids=["k1", "k2", "k3"])

    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k2"]
    assert result.decision.permitted == ["k2"] and result.decision.dropped == ["k1", "k3"]


async def test_a_global_search_is_exactly_the_permitted_set(opensearch, embeddings):
    await retrieve(opensearch, embeddings, "q", scope=scope("kb-b", "kb-a"))
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["kb-a", "kb-b"]

    await retrieve(opensearch, embeddings, "q", scope=RetrievalScope.unrestricted("test"))
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] is None


async def test_no_scope_applies_no_permission_filter(opensearch, embeddings):
    await retrieve(opensearch, embeddings, "q", scope=None, requested_kb_ids=["k1"])
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k1"]


@pytest.mark.parametrize("granted, requested", [((), None), (("other",), ["k1"]), (("k1",), [])])
async def test_nothing_permitted_searches_and_embeds_nothing(opensearch, embeddings, granted, requested):
    result = await retrieve(opensearch, embeddings, "q", scope=scope(*granted), requested_kb_ids=requested)

    assert result.blocked and result.hits == [] and result.total == 0
    opensearch.search_unified.assert_not_called()
    opensearch.search_chunks_vector.assert_not_called()
    embeddings.embed_query.assert_not_called()


async def test_hybrid_embeds_and_fuses(opensearch, embeddings):
    result = await retrieve(opensearch, embeddings, "q", scope=None, mode="hybrid", size=4, min_score=0.2)

    kwargs = opensearch.search_unified.call_args.kwargs
    assert kwargs["query_embedding"] == [0.1, 0.2] and kwargs["use_hybrid"] is True
    assert kwargs["size"] == 4 and kwargs["min_score"] == 0.2
    assert (result.mode, result.total, result.hits) == ("hybrid", 7, [HIT])


async def test_bm25_needs_no_embedding(opensearch, embeddings):
    result = await retrieve(opensearch, embeddings, "q", scope=None, mode="bm25")

    assert opensearch.search_unified.call_args.kwargs["use_hybrid"] is False
    embeddings.embed_query.assert_not_called()
    assert result.mode == "bm25"


async def test_vector_mode_uses_the_vector_search(opensearch, embeddings):
    result = await retrieve(opensearch, embeddings, "q", scope=scope("k1"), mode="vector")

    assert opensearch.search_chunks_vector.call_args.kwargs["knowledge_base_ids"] == ["k1"]
    opensearch.search_unified.assert_not_called()
    assert (result.mode, result.total) == ("vector", 3)


async def test_an_embedding_failure_raises_unless_a_fallback_is_asked_for(opensearch, embeddings):
    embeddings.embed_query.side_effect = RuntimeError("embedder down")

    with pytest.raises(RuntimeError):
        await retrieve(opensearch, embeddings, "q", scope=None)

    result = await retrieve(opensearch, embeddings, "q", scope=None, fall_back_to_bm25=True)
    assert result.mode == "bm25" and opensearch.search_unified.call_args.kwargs["use_hybrid"] is False


async def test_a_supplied_embedding_is_used_as_is(opensearch, embeddings):
    await retrieve(opensearch, embeddings, "q", scope=None, query_embedding=[9.0])

    assert opensearch.search_unified.call_args.kwargs["query_embedding"] == [9.0]
    embeddings.embed_query.assert_not_called()
