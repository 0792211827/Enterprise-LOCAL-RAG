from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document
from src.services.agents.tools import create_retriever_tool


@pytest.mark.asyncio
async def test_create_retriever_tool_basic(mock_opensearch_client, mock_jina_embeddings_client):
    """Test basic retriever tool creation and invocation."""
    tool = create_retriever_tool(
        opensearch_client=mock_opensearch_client,
        embeddings_client=mock_jina_embeddings_client,
        top_k=2,
        use_hybrid=True,
    )

    # Verify tool properties
    assert tool.name == "retrieve_documents"
    assert "Search and return relevant excerpts from the indexed documents" in tool.description

    # Invoke tool
    result = await tool.ainvoke({"query": "machine learning"})

    # Verify result
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(doc, Document) for doc in result)

    # Verify first document
    first_doc = result[0]
    assert first_doc.page_content == "Transformers are neural network architectures based on self-attention mechanisms."
    assert first_doc.metadata["document_id"] == "doc-1706"
    assert first_doc.metadata["title"] == "Attention Is All You Need"
    assert first_doc.metadata["score"] == 0.95

    # Verify embeddings were generated
    mock_jina_embeddings_client.embed_query.assert_called_once_with("machine learning")

    # Verify search was called correctly
    mock_opensearch_client.search_unified.assert_called_once()
    call_args = mock_opensearch_client.search_unified.call_args
    assert call_args.kwargs["query"] == "machine learning"
    assert call_args.kwargs["size"] == 2  # search_unified uses 'size', not 'top_k'
    assert call_args.kwargs["use_hybrid"] is True


@pytest.mark.asyncio
async def test_retriever_tool_empty_results(mock_opensearch_client, mock_jina_embeddings_client):
    """Test retriever tool with no results."""
    from unittest.mock import Mock
    mock_opensearch_client.search_unified = Mock(return_value={"hits": []})

    tool = create_retriever_tool(
        opensearch_client=mock_opensearch_client,
        embeddings_client=mock_jina_embeddings_client,
    )

    result = await tool.ainvoke({"query": "nonexistent topic"})

    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_retriever_tool_custom_top_k(mock_opensearch_client, mock_jina_embeddings_client):
    """Test retriever tool with custom top_k parameter."""
    tool = create_retriever_tool(
        opensearch_client=mock_opensearch_client,
        embeddings_client=mock_jina_embeddings_client,
        top_k=5,
        use_hybrid=False,
    )

    await tool.ainvoke({"query": "test query"})

    call_args = mock_opensearch_client.search_unified.call_args
    # search_unified uses 'size' parameter, not 'top_k'
    assert call_args.kwargs["size"] == 5
    assert call_args.kwargs["use_hybrid"] is False


@pytest.mark.asyncio
async def test_retriever_tool_metadata_fields(mock_opensearch_client, mock_jina_embeddings_client):
    """Test that all expected metadata fields are present."""
    from unittest.mock import Mock
    mock_opensearch_client.search_unified = Mock(return_value={
        "hits": [
            {
                "chunk_text": "Test content",
                "document_id": "doc-2301",
                "title": "Test Document",
                "score": 0.95,
                "section_title": "Introduction",
            }
        ]
    })

    tool = create_retriever_tool(
        opensearch_client=mock_opensearch_client,
        embeddings_client=mock_jina_embeddings_client,
    )

    result = await tool.ainvoke({"query": "test"})

    doc = result[0]
    assert "document_id" in doc.metadata
    assert "title" in doc.metadata
    assert "section" in doc.metadata
    assert "score" in doc.metadata
    assert "search_mode" in doc.metadata
