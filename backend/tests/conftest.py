# Test configuration and shared fixtures
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def mock_embeddings_client():
    """Generic async embedding provider mock."""
    client = AsyncMock()
    client.embed_query = AsyncMock(return_value=[0.1] * 1024)
    client.embed_passages = AsyncMock(return_value=[[0.1] * 1024])
    client.dimension = 1024
    client.model_name = "mock-embedding-model"
    return client


# Backwards-compatible alias used by existing agent tests.
@pytest.fixture
def mock_jina_embeddings_client(mock_embeddings_client):
    return mock_embeddings_client


@pytest.fixture
def mock_opensearch_client():
    """OpenSearch client mock with a sensible default hybrid search result."""
    client = Mock()
    client.health_check = Mock(return_value=True)
    client.search_unified = Mock(
        return_value={
            "hits": [
                {
                    "chunk_text": "Transformers are neural network architectures based on self-attention mechanisms.",
                    "document_id": "doc-1706",
                    "title": "Attention Is All You Need",
                    "authors": "Vaswani et al.",
                    "score": 0.95,
                    "section_title": "Introduction",
                },
                {
                    "chunk_text": "Self-attention allows modeling dependencies without regard to their distance.",
                    "document_id": "doc-1706",
                    "title": "Attention Is All You Need",
                    "authors": "Vaswani et al.",
                    "score": 0.89,
                    "section_title": "Model",
                },
            ]
        }
    )
    return client


@pytest.fixture
def mock_ollama_client():
    """LLM provider mock exposing the OllamaClient surface."""
    client = AsyncMock()
    client.default_model = "llama3.2:1b"
    client.generate = AsyncMock(return_value={"response": "mock answer", "usage_metadata": {}})
    client.generate_rag_answer = AsyncMock(
        return_value={"answer": "mock answer", "sources": [], "confidence": "medium", "citations": []}
    )
    return client


@pytest.fixture
def test_context(mock_opensearch_client, mock_ollama_client, mock_embeddings_client):
    """Runtime Context for agent node tests with mocked dependencies."""
    from src.services.agents.context import Context

    return Context(
        ollama_client=mock_ollama_client,
        opensearch_client=mock_opensearch_client,
        embeddings_client=mock_embeddings_client,
        langfuse_tracer=None,
        langfuse_enabled=False,
        model_name="llama3.2:1b",
        temperature=0.0,
        top_k=3,
        max_retrieval_attempts=2,
        guardrail_threshold=60,
    )


@pytest.fixture
def sample_human_message():
    from langchain_core.messages import HumanMessage

    return HumanMessage(content="What is machine learning?")


@pytest.fixture
def sample_ai_message():
    from langchain_core.messages import AIMessage

    return AIMessage(content="Machine learning is a field of AI focused on learning from data.")


@pytest.fixture
def sample_tool_message():
    from langchain_core.messages import ToolMessage

    return ToolMessage(
        content="Transformers use self-attention to weigh the relevance of tokens in a sequence.",
        tool_call_id="call_1",
    )
