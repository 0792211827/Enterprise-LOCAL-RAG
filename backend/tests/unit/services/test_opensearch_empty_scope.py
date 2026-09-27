"""An empty knowledge-base scope matches nothing; None stays unscoped.

Both are falsy, and the search paths used to treat them identically -- so a
scope that resolved to "no knowledge bases" became a search of all of them.
"""

from unittest.mock import MagicMock

import pytest
from src.services.opensearch.client import OpenSearchClient, scope_matches_nothing

RESPONSE = {"hits": {"total": {"value": 1}, "hits": [{"_id": "c1", "_score": 1.0, "_source": {"chunk_text": "x"}}]}}


@pytest.fixture
def client():
    c = OpenSearchClient.__new__(OpenSearchClient)
    c.client = MagicMock()
    c.client.search = MagicMock(return_value=RESPONSE)
    c.index_name = "test-chunks"
    c.settings = MagicMock()
    c.settings.opensearch.rrf_pipeline_name = "hybrid-rrf-pipeline"
    return c


def test_helper():
    assert scope_matches_nothing([]) is True
    assert scope_matches_nothing(None) is False
    assert scope_matches_nothing(["kb"]) is False


@pytest.mark.parametrize("use_hybrid", [False, True])
def test_unified_empty_scope_returns_nothing_without_querying(client, use_hybrid):
    result = client.search_unified("q", query_embedding=[0.1] * 4, use_hybrid=use_hybrid, knowledge_base_ids=[])
    assert result == {"total": 0, "hits": []}
    client.client.search.assert_not_called()


def test_vector_empty_scope_returns_nothing_without_querying(client):
    assert client.search_chunks_vector([0.1] * 4, knowledge_base_ids=[]) == {"total": 0, "hits": []}
    client.client.search.assert_not_called()


def test_hybrid_empty_scope_returns_nothing_without_querying(client):
    assert client.search_chunks_hybrid("q", [0.1] * 4, knowledge_base_ids=[]) == {"total": 0, "hits": []}
    client.client.search.assert_not_called()


def test_none_scope_still_searches(client):
    result = client.search_unified("q", use_hybrid=False, knowledge_base_ids=None)
    assert result["total"] == 1
    client.client.search.assert_called_once()


def test_results_are_not_shared_between_calls(client):
    first = client.search_unified("q", knowledge_base_ids=[])
    first["hits"].append("mutated")
    assert client.search_unified("q", knowledge_base_ids=[])["hits"] == []
