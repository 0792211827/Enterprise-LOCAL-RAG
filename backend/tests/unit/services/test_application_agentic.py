"""An Application whose strategy is agentic really runs the agentic graph.

Driven through the shared answer module against a recording OpenAI-compatible
backend on a mock transport, with a fake search index, so the whole graph
(guardrail, retrieval, grading, generation) runs for real, offline.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from src.services.access import RetrievalScope
from src.services.applications import NO_PERMITTED_KNOWLEDGE_MESSAGE, answer_for_application
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

HIT = {
    "chunk_id": "c1",
    "chunk_text": "Customers may return products within 14 days.",
    "title": "Returns policy",
    "document_id": "d1",
    "knowledge_base_id": "k2",
    "score": 0.9,
    "section_title": "Returns",
}


class Backend:
    """An OpenAI-compatible server whose guardrail score the test chooses."""

    def __init__(self, guardrail_score=90):
        self.guardrail_score = guardrail_score
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("name")
        content = {
            "GuardrailScoring": json.dumps({"score": self.guardrail_score, "reason": "r"}),
            "GradeDocuments": json.dumps({"binary_score": "yes", "reasoning": "relevant"}),
        }.get(schema, "Fourteen days, from the agent.")
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            },
        )


def make_app(strategy="agentic", kb_ids=("k1", "k2", "k3")):
    return SimpleNamespace(
        slug="bot",
        system_prompt=None,
        rag_strategy=strategy,
        knowledge_bases=[SimpleNamespace(id=k) for k in kb_ids],
        retrieval_configuration=SimpleNamespace(mode="hybrid", top_k=5, score_threshold=None),
        model_configuration=SimpleNamespace(llm_model="served-model"),
    )


def scope(*kb_ids) -> RetrievalScope:
    return RetrievalScope(kb_ids=frozenset(kb_ids), reason="grants")


@pytest.fixture
def backend():
    return Backend()


@pytest.fixture
def provider(backend):
    return OpenAICompatibleLLMProvider("http://backend/v1", "served-model", transport=httpx.MockTransport(backend))


@pytest.fixture
def opensearch():
    client = MagicMock()
    client.search_unified = MagicMock(return_value={"hits": [dict(HIT)]})
    return client


@pytest.fixture
def embeddings():
    client = AsyncMock()
    client.embed_query = AsyncMock(return_value=[0.1] * 8)
    return client


async def test_an_agentic_application_runs_the_graph_on_its_resolved_provider(provider, backend, opensearch, embeddings):
    result = await answer_for_application(make_app(), "How long do I have to return it?", opensearch, embeddings, provider, scope=scope("k1", "k2"))

    assert result.answer == "Fourteen days, from the agent."
    # Guardrail, grading and generation all went to the provider, with the Application's model.
    assert len(backend.requests) >= 3
    assert {json.loads(r.content)["model"] for r in backend.requests} == {"served-model"}
    assert result.model == "served-model"
    assert result.details["agentic"] is True


async def test_graph_retrieval_is_limited_to_the_callers_permitted_attached_knowledge_bases(provider, opensearch, embeddings):
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, provider, scope=scope("k2", "not-attached"))

    assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["k2"]
    assert result.scope_decision.permitted == ["k2"]
    assert result.scope_decision.dropped == ["k1", "k3"]


async def test_what_the_graph_retrieved_is_reported_for_audit_and_citations(provider, opensearch, embeddings):
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, provider, scope=scope("k2"))

    assert [h["document_id"] for h in result.hits] == ["d1"]
    assert result.hits[0]["knowledge_base_id"] == "k2"
    assert result.chunks == [
        {
            "chunk_text": HIT["chunk_text"],
            "title": "Returns policy",
            "score": 0.9,
            "document_id": "d1",
            "section_title": "Returns",
        }
    ]


async def test_a_caller_with_no_grants_is_refused_before_anything_runs(provider, backend, opensearch, embeddings):
    result = await answer_for_application(make_app(), "q", opensearch, embeddings, provider, scope=scope())

    assert result.answer == NO_PERMITTED_KNOWLEDGE_MESSAGE
    assert result.chunks == [] and result.hits == []
    assert backend.requests == []
    opensearch.search_unified.assert_not_called()


async def test_an_out_of_scope_question_is_refused_with_zero_chunks(opensearch, embeddings):
    backend = Backend(guardrail_score=5)
    provider = OpenAICompatibleLLMProvider("http://backend/v1", "served-model", transport=httpx.MockTransport(backend))

    result = await answer_for_application(make_app(), "Who won the match?", opensearch, embeddings, provider, scope=scope("k2"))

    assert "I can only answer questions using the documents" in result.answer
    assert result.chunks == [] and result.hits == []
    opensearch.search_unified.assert_not_called()


async def test_a_traditional_application_is_unchanged(opensearch, embeddings):
    llm = AsyncMock()
    llm.generate_rag_answer = AsyncMock(return_value={"answer": "Traditional answer"})

    result = await answer_for_application(make_app("traditional"), "q", opensearch, embeddings, llm, scope=scope("k2"))

    assert result.answer == "Traditional answer"
    llm.generate_rag_answer.assert_awaited_once()
    llm.get_langchain_model.assert_not_called()
    assert result.details is None
