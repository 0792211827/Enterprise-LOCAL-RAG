from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Async backend for testing."""
    return "asyncio"


def _make_opensearch_mock() -> MagicMock:
    client = MagicMock()
    client.health_check = MagicMock(return_value=True)
    client.get_index_stats = MagicMock(
        return_value={"index_name": "rag-documents-chunks", "document_count": 0}
    )
    client.setup_indices = MagicMock(return_value={"hybrid_index": True})
    client.client = MagicMock()
    client.client.count = MagicMock(return_value={"count": 0})
    default_hits = {
        "total": 1,
        "hits": [
            {
                "chunk_text": "Neural networks are function approximators.",
                "title": "Deep Learning",
                "document_id": "doc-1",
                "score": 0.9,
                "section_title": "Intro",
            }
        ],
    }
    client.search_unified = MagicMock(return_value=default_hits)
    client.search_chunks_hybrid = MagicMock(return_value=default_hits)
    client.search_chunks_vector = MagicMock(return_value=default_hits)
    return client


def _make_llm_mock() -> AsyncMock:
    client = AsyncMock()
    client.default_model = "llama3.2:1b"
    client.health_check = AsyncMock(return_value={"status": "healthy", "message": "ok"})
    client.list_models = AsyncMock(return_value=[{"name": "llama3.2:1b"}])
    client.generate_rag_answer = AsyncMock(
        return_value={"answer": "Mock answer", "sources": [], "confidence": "medium", "citations": []}
    )
    return client


@pytest.fixture
async def client(monkeypatch):
    """HTTP client for API testing with the whole platform mocked (offline)."""
    import src.main as main

    mock_database = MagicMock()
    mock_session = MagicMock()
    mock_database.get_session.return_value.__enter__.return_value = mock_session
    mock_database.get_session.return_value.__exit__.return_value = None

    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 1024)
    embeddings.embed_passages = AsyncMock(return_value=[[0.1] * 1024])

    monkeypatch.setattr(main, "make_database", lambda: mock_database)
    monkeypatch.setattr(main, "make_opensearch_client", _make_opensearch_mock)
    monkeypatch.setattr(main, "make_pdf_parser_service", lambda: MagicMock())
    monkeypatch.setattr(main, "make_embeddings_service", lambda: embeddings)
    monkeypatch.setattr(main, "make_ollama_client", _make_llm_mock)
    monkeypatch.setattr(main, "make_llm_provider", lambda settings: _make_llm_mock())
    monkeypatch.setattr(main, "make_vlm_provider", lambda settings: None)
    monkeypatch.setattr(main, "make_langfuse_tracer", lambda: MagicMock())
    monkeypatch.setattr(main, "make_cache_client", lambda settings: None)

    async with LifespanManager(main.app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as http_client:
            yield http_client
