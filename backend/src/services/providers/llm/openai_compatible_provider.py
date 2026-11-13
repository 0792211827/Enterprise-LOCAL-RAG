import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from src.services.ollama.prompts import RAGPromptBuilder, ResponseParser

from .base import LLMProvider, LLMProviderError
from .citations import build_citations, build_sources

logger = logging.getLogger(__name__)

_SAMPLING_KEYS = ("temperature", "top_p", "max_tokens")


def _error_message(payload: Any) -> Optional[str]:
    """The message of an error payload, or None if ``payload`` is not one.

    Servers disagree on the shape: ``{"error": {"message": ...}}`` (OpenAI, vLLM),
    ``{"error": "..."}`` (TGI, llama.cpp) and ``{"message": ..., "type": "error"}``.
    """
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    if error:
        return str(error)
    return None


class OpenAICompatibleLLMProvider(LLMProvider):
    """LLM provider for any OpenAI-compatible ``/chat/completions`` endpoint.

    Targets local inference servers such as vLLM, TGI, llama.cpp ``llama-server``,
    SGLang, LocalAI and NVIDIA NIM, and Ollama's own ``/v1``. The platform only
    needs an endpoint (including the ``/v1`` suffix), a model name, and an
    optional API key; it does not care how the model is hosted.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
        max_tokens: int = 1024,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        structured_output_method: str = "json_schema",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.default_model = model
        self.max_tokens = max_tokens
        self.structured_output_method = structured_output_method
        self.timeout = httpx.Timeout(timeout)
        self._timeout_seconds = timeout
        self._api_key = api_key
        self._transport = transport
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self.prompt_builder = RAGPromptBuilder()
        self.response_parser = ResponseParser()

    def get_langchain_model(self, model: Optional[str] = None, temperature: float = 0.0, **kwargs: Any) -> Any:
        """A LangChain chat model for the agentic graph, bound to this endpoint.

        Always built with this provider's explicit base URL, so nothing ever
        defaults to api.openai.com.
        """
        if not self.endpoint:
            raise ValueError("An OpenAI-compatible LLM endpoint is required (LLM__ENDPOINT)")
        from .langchain_models import LocalChatOpenAI

        if self._transport is not None:
            kwargs.setdefault("http_async_client", httpx.AsyncClient(transport=self._transport))
        # langchain-openai sends the newer max_completion_tokens, which older
        # local servers ignore (falling back to their own, often tiny, default).
        # Send the classic max_tokens field they all understand.
        extra_body = {"max_tokens": self.max_tokens, **(kwargs.pop("extra_body", None) or {})}
        return LocalChatOpenAI(
            base_url=self.endpoint,
            # The client library insists on a key; local servers usually ignore it.
            api_key=self._api_key or "not-needed",
            model=model or self.default_model,
            temperature=temperature,
            extra_body=extra_body,
            timeout=self._timeout_seconds,
            structured_output_method=self.structured_output_method,
            **kwargs,
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, headers=self._headers, transport=self._transport)

    def _payload(self, model: str, messages: List[Dict[str, str]], stream: bool, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        # Always bound the answer: some servers default to a handful of tokens.
        payload["max_tokens"] = self.max_tokens
        for key in _SAMPLING_KEYS:
            if key in kwargs:
                payload[key] = kwargs[key]
        return payload

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(f"{self.endpoint}/models")
            if response.status_code == 200:
                return {"status": "healthy", "message": "OpenAI-compatible endpoint is running"}
            if response.status_code == 404:
                # Reachable but without a model listing (some servers omit it).
                return {"status": "healthy", "message": "Endpoint reachable (no /models endpoint)"}
            return {"status": "unhealthy", "message": f"Endpoint returned {response.status_code}"}
        except Exception as e:  # noqa: BLE001 - health checks must never raise
            return {"status": "unhealthy", "message": str(e)}

    async def list_models(self) -> List[Dict[str, Any]]:
        async with self._client() as client:
            response = await client.get(f"{self.endpoint}/models")
            response.raise_for_status()
            body = response.json()
        # OpenAI shape is {"data": [...]}; older llama.cpp builds use {"models": [...]}.
        return body.get("data") or body.get("models") or []

    async def _complete(self, model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Dict[str, Any]:
        payload = self._payload(model, messages, False, kwargs)
        start = time.perf_counter()
        async with self._client() as client:
            response = await client.post(f"{self.endpoint}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        message = _error_message(data)
        if message:
            raise LLMProviderError(message)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return {
            "response": text,
            "usage_metadata": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        }

    async def _complete_stream(self, model: str, messages: List[Dict[str, str]], **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        payload = self._payload(model, messages, True, kwargs)
        async with self._client() as client:
            async with client.stream("POST", f"{self.endpoint}/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        yield {"response": "", "done": True}
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    message = _error_message(chunk)
                    if message:
                        raise LLMProviderError(message)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue  # e.g. a trailing usage-only chunk
                    # content is null on role-only and finish chunks
                    yield {"response": (choices[0].get("delta") or {}).get("content") or "", "done": False}

    async def generate(self, model: str, prompt: str, stream: bool = False, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return await self._complete(model, [{"role": "user", "content": prompt}], **kwargs)

    def generate_stream(self, model: str, prompt: str, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        return self._complete_stream(model, [{"role": "user", "content": prompt}], **kwargs)

    async def generate_rag_answer(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: str,
        use_structured_output: bool = False,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        messages = self.prompt_builder.create_rag_messages(query, chunks, system_prompt=system_prompt)
        response = await self._complete(model, messages, temperature=0.7, top_p=0.9)
        return {
            "answer": response["response"],
            "sources": build_sources(chunks),
            "confidence": "medium",
            "citations": build_citations(chunks),
        }

    async def generate_rag_answer_stream(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        messages = self.prompt_builder.create_rag_messages(query, chunks, system_prompt=system_prompt, history=history)
        async for chunk in self._complete_stream(model, messages, temperature=0.7, top_p=0.9):
            yield chunk
