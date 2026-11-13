"""Reranking: contract tests against a fake rerank endpoint (success, error, timeout, disabled)."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from src.config import RerankSettings, Settings
from src.services.access import RetrievalScope
from src.services.rerank.client import HTTPReranker
from src.services.rerank.factory import make_reranker
from src.services.retrieval import retrieve

CHUNKS = ["alpha", "bravo", "charlie", "delta", "echo"]


def hits_for(texts=CHUNKS):
    return [{"chunk_text": t, "title": t.title(), "document_id": f"d-{t}", "score": 1.0 - i / 10} for i, t in enumerate(texts)]


class FakeEndpoint:
    """A Cohere/Jina/vLLM-shaped rerank server: scores come from ``ranking`` (best first, by chunk text)."""

    def __init__(self, ranking=("echo", "charlie", "alpha", "bravo", "delta"), status=200, raises=None, body=None):
        self.ranking, self.status, self.raises, self.body = list(ranking), status, raises, body
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        payload = json.loads(request.content)
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        documents = payload["documents"]
        results = [
            {"index": documents.index(text), "relevance_score": 1.0 - rank / 10}
            for rank, text in enumerate(self.ranking)
            if text in documents
        ]
        return httpx.Response(self.status, json={"results": results})

    def sent(self):
        return [json.loads(r.content) for r in self.requests]


def reranker_for(endpoint, candidates=5, api_key=None):
    return HTTPReranker(
        endpoint="http://rerank.internal/v1/rerank",
        model="bge-reranker",
        api_key=api_key,
        candidates=candidates,
        timeout=1.0,
        transport=httpx.MockTransport(endpoint),
    )


@pytest.fixture
def opensearch():
    client = MagicMock()
    client.search_unified = MagicMock(side_effect=lambda **kw: {"total": 5, "hits": hits_for()[: kw["size"]]})
    return client


@pytest.fixture
def embeddings():
    client = AsyncMock()
    client.embed_query = AsyncMock(return_value=[0.1])
    return client


def texts(result):
    return [h["chunk_text"] for h in result.hits]


async def test_reranked_passages_are_reordered_and_cut_to_the_requested_count(opensearch, embeddings):
    endpoint = FakeEndpoint()
    result = await retrieve(opensearch, embeddings, "which?", scope=None, size=2, reranker=reranker_for(endpoint))

    assert texts(result) == ["echo", "charlie"]
    assert result.rerank == "applied"
    # Candidates (5) were fetched, not just the 2 asked for, so reranking has something to reorder.
    assert opensearch.search_unified.call_args.kwargs["size"] == 5
    request = endpoint.sent()[0]
    assert request["query"] == "which?" and request["model"] == "bge-reranker"
    assert request["documents"] == CHUNKS


async def test_the_candidate_count_never_shrinks_below_the_requested_count(opensearch, embeddings):
    await retrieve(opensearch, embeddings, "q", scope=None, size=8, reranker=reranker_for(FakeEndpoint(), candidates=3))
    assert opensearch.search_unified.call_args.kwargs["size"] == 8


async def test_the_api_key_is_sent_as_a_bearer_token(opensearch, embeddings):
    endpoint = FakeEndpoint()
    await retrieve(opensearch, embeddings, "q", scope=None, reranker=reranker_for(endpoint, api_key="sk-r"))
    assert endpoint.requests[0].headers["authorization"] == "Bearer sk-r"


async def test_disabled_changes_nothing(opensearch, embeddings):
    result = await retrieve(opensearch, embeddings, "q", scope=None, size=3, reranker=None)

    assert texts(result) == ["alpha", "bravo", "charlie"]
    assert result.rerank is None
    assert opensearch.search_unified.call_args.kwargs["size"] == 3


@pytest.mark.parametrize(
    "endpoint",
    [
        FakeEndpoint(status=503, body={"error": "overloaded"}),
        FakeEndpoint(raises=httpx.ReadTimeout("too slow")),
        FakeEndpoint(raises=httpx.ConnectError("down")),
        FakeEndpoint(body={"unexpected": "shape"}),
        FakeEndpoint(body={"results": [{"index": 99, "relevance_score": 1.0}]}),
    ],
    ids=["http-error", "timeout", "unreachable", "bad-shape", "bad-index"],
)
async def test_a_failing_endpoint_falls_back_to_the_original_order_and_says_so(opensearch, embeddings, endpoint, caplog):
    with caplog.at_level(logging.WARNING):
        result = await retrieve(opensearch, embeddings, "q", scope=None, size=3, reranker=reranker_for(endpoint))

    assert texts(result) == ["alpha", "bravo", "charlie"]
    assert result.rerank == "skipped"
    assert any("rerank" in record.message.lower() for record in caplog.records)


async def test_the_endpoint_only_sees_permitted_passages_and_cannot_widen_access(opensearch, embeddings):
    # The index applies the scope; what comes back is all the reranker can ever see or return.
    opensearch.search_unified = MagicMock(return_value={"total": 2, "hits": hits_for(["alpha", "bravo"])})
    endpoint = FakeEndpoint(
        body={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}, {"index": 7, "relevance_score": 0.4}]}
    )

    result = await retrieve(
        opensearch, embeddings, "q", scope=RetrievalScope(kb_ids=frozenset({"k1"}), reason="grants"), requested_kb_ids=["k1", "k2"],
        reranker=reranker_for(endpoint),
    )

    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k1"]
    assert endpoint.sent()[0]["documents"] == ["alpha", "bravo"]
    # An out-of-range index is a malformed answer, not a licence to invent passages.
    assert result.rerank == "skipped" and texts(result) == ["alpha", "bravo"]


async def test_a_blocked_search_never_calls_the_endpoint(opensearch, embeddings):
    endpoint = FakeEndpoint()
    result = await retrieve(
        opensearch, embeddings, "q", scope=RetrievalScope(kb_ids=frozenset(), reason="grants"), reranker=reranker_for(endpoint)
    )
    assert result.blocked and endpoint.requests == []


async def test_pages_after_the_first_are_not_reranked(opensearch, embeddings):
    endpoint = FakeEndpoint()
    result = await retrieve(opensearch, embeddings, "q", scope=None, size=3, from_=3, reranker=reranker_for(endpoint))
    assert endpoint.requests == [] and result.rerank is None


def test_off_by_default():
    assert Settings().rerank.enabled is False
    assert make_reranker(Settings()) is None


def test_enabled_settings_build_a_reranker():
    settings = Settings(
        rerank=RerankSettings(enabled=True, endpoint="http://r/v1/rerank", model="m", candidates=40, timeout=2.5, api_key="k")
    )
    reranker = make_reranker(settings)
    assert (reranker.endpoint, reranker.candidates, reranker.timeout) == ("http://r/v1/rerank", 40, 2.5)


def test_enabling_without_an_endpoint_is_refused_clearly():
    with pytest.raises(ValueError, match="RERANK__ENDPOINT"):
        make_reranker(Settings(rerank=RerankSettings(enabled=True, endpoint="")))
