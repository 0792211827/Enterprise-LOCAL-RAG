"""Retrieval must be scoped to an application's knowledge bases.

Without these filters every application searches one global index and answers
from other applications' documents.
"""

from unittest.mock import MagicMock

import pytest
from src.services.opensearch.client import KNN_MAX_CANDIDATES, OpenSearchClient, knn_k
from src.services.opensearch.query_builder import QueryBuilder

KB_IDS = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
EMPTY_RESPONSE = {"hits": {"total": {"value": 0}, "hits": []}}


@pytest.fixture
def client():
    c = OpenSearchClient.__new__(OpenSearchClient)
    c.client = MagicMock()
    c.client.search = MagicMock(return_value=EMPTY_RESPONSE)
    c.index_name = "test-chunks"
    return c


def _filters(body):
    return body["query"]["bool"].get("filter", [])


def test_query_builder_adds_kb_filter():
    body = QueryBuilder(query="q", search_chunks=True, knowledge_base_ids=KB_IDS).build()
    assert {"terms": {"knowledge_base_id": KB_IDS}} in _filters(body)


def test_query_builder_omits_filter_when_unscoped():
    body = QueryBuilder(query="q", search_chunks=True).build()
    assert all("knowledge_base_id" not in str(f) for f in _filters(body))


def test_bm25_search_is_scoped(client):
    client.search_unified(query="q", size=5, use_hybrid=False, knowledge_base_ids=KB_IDS)
    body = client.client.search.call_args.kwargs["body"]
    assert {"terms": {"knowledge_base_id": KB_IDS}} in _filters(body)


def test_hybrid_search_scopes_both_legs(client):
    """RRF fuses two independent legs; an unfiltered kNN leg leaks documents."""
    client.search_unified(query="q", query_embedding=[0.1] * 8, size=5, use_hybrid=True, knowledge_base_ids=KB_IDS)
    body = client.client.search.call_args.kwargs["body"]
    bm25_leg, knn_leg = body["query"]["hybrid"]["queries"]

    assert {"terms": {"knowledge_base_id": KB_IDS}} in bm25_leg["bool"]["filter"]
    assert {"terms": {"knowledge_base_id": KB_IDS}} in knn_leg["bool"]["filter"]
    assert "knn" in knn_leg["bool"]["must"][0]


def test_hybrid_knn_leg_is_bare_when_unscoped(client):
    client.search_unified(query="q", query_embedding=[0.1] * 8, size=5, use_hybrid=True)
    body = client.client.search.call_args.kwargs["body"]
    _bm25, knn_leg = body["query"]["hybrid"]["queries"]
    assert "knn" in knn_leg, "unscoped searches must not pay for a bool wrapper"


def test_vector_search_is_scoped(client):
    client.search_chunks_vector(query_embedding=[0.1] * 8, size=5, knowledge_base_ids=KB_IDS)
    body = client.client.search.call_args.kwargs["body"]
    assert {"terms": {"knowledge_base_id": KB_IDS}} in body["query"]["bool"]["filter"]


def test_knn_oversamples_only_when_filtered():
    """nmslib post-filters, so a filtered ANN query needs a wider candidate set."""
    assert knn_k(10, []) == 10
    assert knn_k(10, [{"terms": {"knowledge_base_id": KB_IDS}}]) == 100
    assert knn_k(500, [{"terms": {}}]) == KNN_MAX_CANDIDATES
