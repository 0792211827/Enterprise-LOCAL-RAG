"""The agentic graph takes its LLM from the configured provider, not always Ollama.

The four LLM-using nodes run for real against an OpenAICompatibleLLMProvider on
a mock transport, with an Ollama client that fails the test if it is touched.
"""

import json
import socket
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from src.config import LLMSettings, Settings
from src.dependencies import get_agentic_rag_service
from src.services.agents.context import Context
from src.services.agents.factory import make_agentic_rag_service
from src.services.agents.nodes import (
    ainvoke_generate_answer_step,
    ainvoke_grade_documents_step,
    ainvoke_guardrail_step,
    ainvoke_rewrite_query_step,
)
from src.services.providers.llm.base import LLMProvider
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

STRUCTURED_REPLIES = {
    "GuardrailScoring": {"score": 88, "reason": "in scope"},
    "GradeDocuments": {"binary_score": "yes", "reasoning": "relevant"},
    "QueryRewriteOutput": {"rewritten_query": "rewritten by the backend", "reasoning": "clearer"},
}


class Backend:
    """A recording OpenAI-compatible server that answers the agent's structured calls."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        response_format = body.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            content = json.dumps(STRUCTURED_REPLIES[response_format["json_schema"]["name"]])
        elif response_format.get("type") == "json_object":
            content = json.dumps(next(iter(STRUCTURED_REPLIES.values())))
        else:
            content = "A plain answer from the backend."
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

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def backend():
    return Backend()


@pytest.fixture
def provider(backend):
    return OpenAICompatibleLLMProvider(
        "http://backend/v1", "served-model", max_tokens=321, transport=httpx.MockTransport(backend)
    )


@pytest.fixture
def ollama_must_not_be_used():
    ollama = Mock()
    ollama.get_langchain_model.side_effect = AssertionError("the agent used Ollama instead of the configured provider")
    return ollama


def _runtime(provider, ollama) -> Runtime:
    runtime = Mock(spec=Runtime)
    runtime.context = Context(
        ollama_client=ollama,
        opensearch_client=Mock(),
        embeddings_client=Mock(),
        langfuse_tracer=None,
        model_name="served-model",
        llm_provider=provider,
    )
    return runtime


QUESTION = HumanMessage(content="How many days do I have to return a product?")
EXCERPT = ToolMessage(content="Customers may return products within 14 days.", tool_call_id="call_1")


class TestNodesUseTheConfiguredProvider:
    async def test_guardrail(self, provider, backend, ollama_must_not_be_used):
        state = {"messages": [QUESTION], "retrieval_attempts": 0}
        result = await ainvoke_guardrail_step(state, _runtime(provider, ollama_must_not_be_used))
        # 88 comes from the backend; a failed call would have fallen back to 50.
        assert result["guardrail_result"].score == 88

    async def test_grade_documents(self, provider, backend, ollama_must_not_be_used):
        state = {"messages": [QUESTION, EXCERPT], "retrieval_attempts": 1}
        result = await ainvoke_grade_documents_step(state, _runtime(provider, ollama_must_not_be_used))
        assert result["routing_decision"] == "generate_answer"
        # The reasoning text only the backend supplies; the fallback heuristic words it differently.
        assert result["grading_results"][0].reasoning == "relevant"

    async def test_rewrite_query(self, provider, backend, ollama_must_not_be_used):
        state = {"messages": [QUESTION], "retrieval_attempts": 1}
        result = await ainvoke_rewrite_query_step(state, _runtime(provider, ollama_must_not_be_used))
        assert result["rewritten_query"] == "rewritten by the backend"

    async def test_generate_answer(self, provider, backend, ollama_must_not_be_used):
        state = {"messages": [QUESTION, EXCERPT], "retrieval_attempts": 1}
        result = await ainvoke_generate_answer_step(state, _runtime(provider, ollama_must_not_be_used))
        assert result["messages"][0].content == "A plain answer from the backend."

    async def test_every_call_targets_the_backend_with_the_graph_model_and_token_limit(
        self, provider, backend, ollama_must_not_be_used
    ):
        runtime = _runtime(provider, ollama_must_not_be_used)
        await ainvoke_guardrail_step({"messages": [QUESTION], "retrieval_attempts": 0}, runtime)
        await ainvoke_generate_answer_step({"messages": [QUESTION, EXCERPT], "retrieval_attempts": 1}, runtime)

        assert {str(r.url) for r in backend.requests} == {"http://backend/v1/chat/completions"}
        for body in backend.bodies():
            assert body["model"] == "served-model"
            # The classic field every local server understands, not max_completion_tokens.
            assert body["max_tokens"] == 321
            assert "max_completion_tokens" not in body

    async def test_no_network_beyond_the_configured_endpoint(self, provider, backend, ollama_must_not_be_used, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("unexpected network access")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        runtime = _runtime(provider, ollama_must_not_be_used)
        result = await ainvoke_guardrail_step({"messages": [QUESTION], "retrieval_attempts": 0}, runtime)
        assert result["guardrail_result"].score == 88


class TestStructuredOutputMethod:
    @pytest.mark.parametrize(
        "method, expected_format",
        [("json_schema", "json_schema"), ("json_mode", "json_object")],
    )
    async def test_configured_method_is_what_the_server_is_asked_for(self, backend, method, expected_format):
        provider = OpenAICompatibleLLMProvider(
            "http://backend/v1", "m", transport=httpx.MockTransport(backend), structured_output_method=method
        )
        from src.services.agents.models import GuardrailScoring

        await provider.get_langchain_model().with_structured_output(GuardrailScoring).ainvoke("rate this (as JSON)")
        assert backend.bodies()[0]["response_format"]["type"] == expected_format

    def test_setting_reaches_the_provider(self):
        from src.services.providers.llm.factory import make_llm_provider

        settings = Settings(llm=LLMSettings(provider="vllm", endpoint="http://x/v1", structured_output_method="json_mode"))
        assert make_llm_provider(settings).structured_output_method == "json_mode"


class TestProviderContract:
    def test_a_provider_without_a_chat_model_fails_clearly(self):
        class Minimal(LLMProvider):
            async def health_check(self): ...
            async def list_models(self): ...
            async def generate(self, model, prompt, stream=False, **kwargs): ...
            def generate_stream(self, model, prompt, **kwargs): ...
            async def generate_rag_answer(self, query, chunks, model, use_structured_output=False, system_prompt=None): ...
            def generate_rag_answer_stream(self, query, chunks, model, system_prompt=None): ...

        with pytest.raises(NotImplementedError, match="agentic graph"):
            Minimal().get_langchain_model()

    def test_empty_endpoint_is_refused_rather_than_defaulting_to_a_hosted_api(self):
        with pytest.raises(ValueError, match="endpoint"):
            OpenAICompatibleLLMProvider("", "m").get_langchain_model()

    def test_chat_model_is_bound_to_the_explicit_endpoint_and_key(self):
        model = OpenAICompatibleLLMProvider("http://backend/v1/", "m", api_key="local-key").get_langchain_model(model="other")
        assert model.openai_api_base == "http://backend/v1"
        assert model.model_name == "other"
        assert model.openai_api_key.get_secret_value() == "local-key"


class TestContextAndWiring:
    def test_context_prefers_the_provider_and_falls_back_to_ollama(self):
        ollama, provider = Mock(), Mock()
        kwargs = dict(opensearch_client=Mock(), embeddings_client=Mock(), langfuse_tracer=None)
        assert Context(ollama_client=ollama, llm_provider=provider, **kwargs).chat_model_source is provider
        assert Context(ollama_client=ollama, **kwargs).chat_model_source is ollama

    def test_factory_hands_the_provider_to_the_service(self):
        provider = Mock()
        service = make_agentic_rag_service(
            opensearch_client=Mock(), ollama_client=Mock(), embeddings_client=Mock(), llm_provider=provider
        )
        assert service.llm_provider is provider

    def test_dependency_passes_the_provider_and_its_model_default(self):
        provider = Mock()
        settings = Settings(llm=LLMSettings(provider="vllm", endpoint="http://x/v1", model="qwen"))
        service = get_agentic_rag_service(Mock(), Mock(), Mock(), Mock(), settings, None, provider)
        assert service.llm_provider is provider
        assert service.graph_config.model == "qwen"

    def test_default_model_follows_the_provider(self):
        assert Settings().default_llm_model == Settings().ollama_model
        assert Settings(llm=LLMSettings(provider="vllm", model="qwen")).default_llm_model == "qwen"
