"""Contract tests for OpenAICompatibleLLMProvider.

The response shapes below are modelled on what vLLM, TGI, llama.cpp
``llama-server`` and Ollama's ``/v1`` return, so the provider is exercised
against the differences between them without needing any of those servers.
They are fixtures of documented behaviour, not proof of a live run; the live
status of each backend is tracked in docs/deployment/inference-backends.md.
"""

import json

import httpx
import pytest
from src.config import LLMSettings, Settings
from src.services.ollama.prompts import RAGPromptBuilder
from src.services.providers.llm.base import LLMProviderError
from src.services.providers.llm.factory import make_llm_provider
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

CHUNKS = [{"chunk_text": "Refunds take 14 days.", "title": "Policy", "section_title": "Refunds"}]


def _provider(handler, **kwargs) -> tuple[OpenAICompatibleLLMProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = OpenAICompatibleLLMProvider(
        endpoint="http://backend/v1/", model="m", transport=httpx.MockTransport(recording), **kwargs
    )
    return provider, seen


def _sse(*events) -> bytes:
    lines = [e if isinstance(e, str) else f"data: {json.dumps(e)}" for e in events]
    return ("\n\n".join(lines) + "\n\n").encode()


def _chat(content, usage=True):
    body = {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    return httpx.Response(200, json=body)


def _delta(content, finish=None):
    return {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]}


async def _collect(stream):
    return [chunk async for chunk in stream]


class TestRequest:
    async def test_rag_answer_sends_system_role_and_max_tokens(self):
        provider, seen = _provider(lambda r: _chat("Refunds take 14 days [1]."))
        result = await provider.generate_rag_answer("How long are refunds?", CHUNKS, model="qwen")

        body = json.loads(seen[0].content)
        assert str(seen[0].url) == "http://backend/v1/chat/completions"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert "Refunds take 14 days." in body["messages"][1]["content"]
        assert body["messages"][0]["content"] not in body["messages"][1]["content"]
        assert body["max_tokens"] == 1024
        assert body["stream"] is False and body["model"] == "qwen"
        assert result["answer"] == "Refunds take 14 days [1]."

    async def test_custom_system_prompt_becomes_the_system_message(self):
        provider, seen = _provider(lambda r: _chat("ok"))
        await provider.generate_rag_answer("q", CHUNKS, model="m", system_prompt="Answer in French.")
        assert json.loads(seen[0].content)["messages"][0] == {"role": "system", "content": "Answer in French."}

    async def test_max_tokens_comes_from_configuration_and_callers_may_override(self):
        provider, seen = _provider(lambda r: _chat("ok"), max_tokens=256)
        await provider.generate("m", "hi")
        await provider.generate("m", "hi", max_tokens=32)
        assert [json.loads(r.content)["max_tokens"] for r in seen] == [256, 32]

    async def test_raw_prompt_is_a_single_user_message(self):
        provider, seen = _provider(lambda r: _chat("ok"))
        await provider.generate("m", "hello")
        assert json.loads(seen[0].content)["messages"] == [{"role": "user", "content": "hello"}]

    async def test_api_key_is_sent_as_bearer_token(self):
        provider, seen = _provider(lambda r: _chat("ok"), api_key="local-key")
        await provider.generate("m", "hi")
        assert seen[0].headers["authorization"] == "Bearer local-key"

    async def test_usage_is_parsed_and_null_content_becomes_empty(self):
        provider, _ = _provider(lambda r: _chat(None))
        result = await provider.generate("m", "hi")
        assert result["response"] == ""
        assert result["usage_metadata"]["total_tokens"] == 10


class TestStreaming:
    async def test_vllm_style_stream_skips_role_null_and_usage_chunks(self):
        events = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
            _delta("Refunds "),
            _delta("take 14 days."),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 10}},
            "data: [DONE]",
        ]
        provider, seen = _provider(lambda r: httpx.Response(200, content=_sse(*events)))
        chunks = await _collect(provider.generate_rag_answer_stream("q", CHUNKS, model="m"))

        assert "".join(c["response"] for c in chunks) == "Refunds take 14 days."
        assert chunks[-1] == {"response": "", "done": True}
        assert all(isinstance(c["response"], str) for c in chunks)
        body = json.loads(seen[0].content)
        assert body["stream"] is True and body["messages"][0]["role"] == "system"

    async def test_earlier_turns_of_a_conversation_are_sent_as_chat_messages_before_the_question(self):
        provider, seen = _provider(lambda r: httpx.Response(200, content=_sse(_delta("ok"), "data: [DONE]")))
        history = [{"role": "user", "content": "Refund window?"}, {"role": "assistant", "content": "14 days."}]
        await _collect(provider.generate_rag_answer_stream("and for EU?", CHUNKS, model="m", history=history))

        roles = [(m["role"], m["content"][:14]) for m in json.loads(seen[0].content)["messages"]]
        assert roles[1:3] == [("user", "Refund window?"), ("assistant", "14 days.")]
        assert roles[0][0] == "system" and roles[3][0] == "user" and len(roles) == 4

    @pytest.mark.parametrize(
        "error_event",
        [
            {"error": "Request failed during generation: out of memory", "error_type": "generation"},  # TGI
            {"error": {"code": 500, "message": "context overflow", "type": "server_error"}},  # llama.cpp / OpenAI
        ],
        ids=["tgi", "llama-cpp"],
    )
    async def test_sse_error_payload_raises_instead_of_yielding_an_empty_answer(self, error_event):
        provider, _ = _provider(lambda r: httpx.Response(200, content=_sse(_delta("partial"), error_event)))
        received = []
        with pytest.raises(LLMProviderError, match="memory|overflow"):
            async for chunk in provider.generate_stream("m", "hi"):
                received.append(chunk["response"])
        assert received == ["partial"]

    async def test_malformed_and_comment_lines_are_ignored(self):
        body = b": keep-alive\n\ndata: not-json\n\n" + _sse(_delta("ok"), "data: [DONE]")
        provider, _ = _provider(lambda r: httpx.Response(200, content=body))
        chunks = await _collect(provider.generate_stream("m", "hi"))
        assert "".join(c["response"] for c in chunks) == "ok"


class TestErrors:
    async def test_error_body_on_a_200_response_raises(self):
        provider, _ = _provider(lambda r: httpx.Response(200, json={"error": {"message": "model not loaded"}}))
        with pytest.raises(LLMProviderError, match="model not loaded"):
            await provider.generate("m", "hi")

    async def test_http_errors_propagate(self):
        provider, _ = _provider(lambda r: httpx.Response(422, json={"error": "bad request", "error_type": "validation"}))
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate("m", "hi")

    async def test_http_error_on_stream_propagates(self):
        provider, _ = _provider(lambda r: httpx.Response(503, content=b"loading"))
        with pytest.raises(httpx.HTTPStatusError):
            await _collect(provider.generate_stream("m", "hi"))


class TestHealthAndModels:
    async def test_healthy_when_models_listed(self):
        provider, _ = _provider(lambda r: httpx.Response(200, json={"data": []}))
        assert (await provider.health_check())["status"] == "healthy"

    async def test_models_404_means_reachable_not_unhealthy(self):
        provider, _ = _provider(lambda r: httpx.Response(404))
        health = await provider.health_check()
        assert health["status"] == "healthy"
        assert "no /models" in health["message"]

    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_other_statuses_are_unhealthy(self, status):
        provider, _ = _provider(lambda r: httpx.Response(status))
        assert (await provider.health_check())["status"] == "unhealthy"

    async def test_connection_failure_never_raises(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        provider, _ = _provider(refuse)
        health = await provider.health_check()
        assert health["status"] == "unhealthy" and "refused" in health["message"]

    async def test_list_models_reads_openai_and_older_llama_cpp_shapes(self):
        openai, _ = _provider(lambda r: httpx.Response(200, json={"object": "list", "data": [{"id": "a"}]}))
        llama, _ = _provider(lambda r: httpx.Response(200, json={"models": [{"name": "b"}]}))
        assert await openai.list_models() == [{"id": "a"}]
        assert await llama.list_models() == [{"name": "b"}]


class TestBackendCheckCli:
    @staticmethod
    def _backend(answer_ok=True, known_model="m"):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": known_model}]})
            body = json.loads(request.content)
            if body["model"] != known_model:
                return httpx.Response(404, json={"error": "unknown model"})
            if body["stream"]:
                text = "14 days" if answer_ok else "no idea"
                return httpx.Response(200, content=_sse(_delta(text), "data: [DONE]"))
            return _chat("14 days" if answer_ok else "no idea")

        return httpx.MockTransport(handler)

    async def test_all_checks_pass_against_a_conforming_backend(self):
        from src.cli.check_llm_backend import run_checks

        results = await run_checks("http://backend/v1", "m", transport=self._backend())
        assert {status for status, _, _ in results} == {"PASS"}

    async def test_a_wrong_answer_fails_a_required_check(self):
        from src.cli.check_llm_backend import run_checks

        results = await run_checks("http://backend/v1", "m", transport=self._backend(answer_ok=False))
        failed = {name for status, name, _ in results if status == "FAIL"}
        assert failed == {"grounded RAG answer"}

    async def test_servers_that_ignore_the_model_name_are_informational_only(self):
        from src.cli.check_llm_backend import run_checks

        def serve_anything(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(404)
            if json.loads(request.content)["stream"]:
                return httpx.Response(200, content=_sse(_delta("14 days"), "data: [DONE]"))
            return _chat("14 days")

        results = await run_checks("http://backend/v1", "m", transport=httpx.MockTransport(serve_anything))
        assert not [r for r in results if r[0] == "FAIL"]
        assert {name for status, name, _ in results if status == "INFO"} == {"list models", "unknown model is rejected"}


class TestPromptsAndConfiguration:
    def test_messages_carry_exactly_the_prompt_the_ollama_path_sends(self):
        builder = RAGPromptBuilder()
        system, user = builder.create_rag_messages("q?", CHUNKS)
        assert builder.create_rag_prompt("q?", CHUNKS) == f"{system['content']}\n\n{user['content']}"

    def test_max_tokens_setting_reaches_the_provider(self):
        assert LLMSettings().max_tokens == 1024
        settings = Settings(llm=LLMSettings(provider="vllm", endpoint="http://x/v1", model="m", max_tokens=300))
        assert make_llm_provider(settings).max_tokens == 300
