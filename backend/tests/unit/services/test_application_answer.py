"""The shared answer path used by both the playground and /v1/chat/completions."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.applications import (
    NO_KNOWLEDGE_BASE_MESSAGE,
    answer_for_application,
    effective_system_prompt,
)


def make_app(kb_ids=("k1",), mode="hybrid", top_k=8, system_prompt=None):
    return SimpleNamespace(
        slug="bot",
        system_prompt=system_prompt,
        streaming_enabled=True,
        citations_enabled=True,
        knowledge_bases=[SimpleNamespace(id=k) for k in kb_ids],
        retrieval_configuration=SimpleNamespace(mode=mode, top_k=top_k, score_threshold=None),
        model_configuration=SimpleNamespace(llm_model="qwen2.5:7b"),
    )


@pytest.fixture
def collaborators():
    opensearch = MagicMock()
    opensearch.search_unified = MagicMock(
        return_value={"hits": [{"chunk_text": "text", "title": "T", "score": 1.0, "document_id": "d"}]}
    )
    opensearch.search_chunks_vector = MagicMock(return_value={"hits": []})
    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 8)
    llm = AsyncMock()
    llm.generate_rag_answer = AsyncMock(return_value={"answer": "ok"})
    return opensearch, embeddings, llm


async def test_scopes_search_to_the_applications_knowledge_bases(collaborators):
    opensearch, embeddings, llm = collaborators
    kb_id = uuid.uuid4()
    await answer_for_application(make_app([kb_id]), "q", opensearch, embeddings, llm)
    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == [str(kb_id)]


async def test_application_with_no_knowledge_bases_retrieves_nothing(collaborators):
    """The critical case: an empty list must not degrade to an unscoped search."""
    opensearch, embeddings, llm = collaborators
    result = await answer_for_application(make_app(kb_ids=[]), "q", opensearch, embeddings, llm)

    assert result.answer == NO_KNOWLEDGE_BASE_MESSAGE
    assert result.chunks == []
    opensearch.search_unified.assert_not_called()
    opensearch.search_chunks_vector.assert_not_called()
    llm.generate_rag_answer.assert_not_called()


async def test_bm25_mode_skips_embedding(collaborators):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(make_app(mode="bm25"), "q", opensearch, embeddings, llm)
    embeddings.embed_query.assert_not_awaited()
    assert opensearch.search_unified.call_args.kwargs["use_hybrid"] is False


async def test_vector_mode_scopes_too(collaborators):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(make_app(mode="vector"), "q", opensearch, embeddings, llm)
    assert opensearch.search_chunks_vector.call_args.kwargs["knowledge_base_ids"] == ["k1"]


async def test_system_prompt_is_forwarded(collaborators):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(make_app(system_prompt="Be terse."), "q", opensearch, embeddings, llm)
    assert llm.generate_rag_answer.call_args.kwargs["system_prompt"] == "Be terse."


async def test_top_k_override_wins(collaborators):
    opensearch, embeddings, llm = collaborators
    await answer_for_application(make_app(), "q", opensearch, embeddings, llm, top_k_override=3)
    assert opensearch.search_unified.call_args.kwargs["size"] == 3


def test_effective_system_prompt_composition():
    app = make_app(system_prompt="Base.")
    assert effective_system_prompt(app) == "Base."
    assert effective_system_prompt(app, "Extra.") == "Base.\n\nExtra."
    assert effective_system_prompt(make_app(), "Only.") == "Only."
    # Composition must not write back to the ORM object.
    assert app.system_prompt == "Base."
