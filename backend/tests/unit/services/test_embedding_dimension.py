"""The embedding dimension setting drives the chunk index mapping."""

import warnings
from unittest.mock import MagicMock

from src.config import Settings
from src.services.opensearch.client import OpenSearchClient
from src.services.opensearch.dimension_guard import check_index_dimension


def _created_dimension(settings: Settings) -> int:
    client = OpenSearchClient(host="http://localhost:9200", settings=settings)
    client.client = MagicMock()
    client.client.indices.exists.return_value = False
    client._create_hybrid_index()
    body = client.client.indices.create.call_args.kwargs["body"]
    return body["mappings"]["properties"]["embedding"]["dimension"]


def test_default_mapping_dimension_is_unchanged():
    assert _created_dimension(Settings()) == 1024


def test_new_index_follows_embedding_dimension(monkeypatch):
    monkeypatch.setenv("EMBEDDING__DIMENSION", "768")
    assert _created_dimension(Settings()) == 768


def test_deprecated_vector_dimension_does_not_drive_the_mapping(monkeypatch):
    monkeypatch.setenv("EMBEDDING__DIMENSION", "768")
    monkeypatch.setenv("OPENSEARCH__VECTOR_DIMENSION", "512")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert _created_dimension(Settings()) == 768


def test_deprecated_vector_dimension_warns_when_it_disagrees(monkeypatch):
    monkeypatch.setenv("OPENSEARCH__VECTOR_DIMENSION", "512")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Settings()
    assert any("OPENSEARCH__VECTOR_DIMENSION" in str(w.message) for w in caught)


def test_deprecated_vector_dimension_is_quiet_when_it_agrees(monkeypatch):
    monkeypatch.setenv("OPENSEARCH__VECTOR_DIMENSION", "1024")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Settings()
    assert not any("OPENSEARCH__VECTOR_DIMENSION" in str(w.message) for w in caught)


def _opensearch(index_dimension):
    client = MagicMock()
    client.index_name = "rag-documents-chunks"
    client.get_embedding_dimension.return_value = index_dimension
    return client


def test_matching_index_sets_no_guard():
    assert check_index_dimension(_opensearch(1024), configured=1024) is None


def test_missing_index_sets_no_guard():
    assert check_index_dimension(_opensearch(None), configured=1024) is None


def test_mismatch_explains_the_fix():
    message = check_index_dimension(_opensearch(1024), configured=768)
    assert "1024" in message and "768" in message
    assert "reindex" in message.lower()


def test_unreachable_opensearch_sets_no_guard():
    client = _opensearch(None)
    client.get_embedding_dimension.side_effect = RuntimeError("down")
    assert check_index_dimension(client, configured=768) is None
