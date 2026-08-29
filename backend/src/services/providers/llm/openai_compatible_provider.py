import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from src.services.ollama.prompts import RAGPromptBuilder, ResponseParser

from .base import LLMProvider
from .citations import build_citations, build_sources

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMProvider(LLMProvider):
    """LLM provider for any OpenAI-compatible ``/chat/completions`` endpoint.

    Compatible with local inference servers such as vLLM, SGLang, LocalAI, and
    NVIDIA NIM. The platform only needs an endpoint, a model name, and an
    optional API key; it does not care how the model is hosted.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.default_model = model
        self.timeout = httpx.Timeout(timeout)
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self.prompt_builder = RAGPromptBuilder()
        self.response_parser = ResponseParser()

    def _messages(self, prompt: str) -> List[Dict[str, str]]:
        return [{"role": "user", "content": prompt}]

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
                response = await client.get(f"{self.endpoint}/models")
                if response.status_code == 200:
                    return {"status": "healthy", "message": "OpenAI-compatible endpoint is running"}
                return {"status": "unhealthy", "message": f"Endpoint returned {response.status_code}"}
        except Exception as e:  # noqa: BLE001 - health checks must never raise
            return {"status": "unhealthy", "message": str(e)}

    async def list_models(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
            response = await client.get(f"{self.endpoint}/models")
            response.raise_for_status()
            return response.json().get("data", [])

    async def generate(
        self, model: str, prompt: str, stream: bool = False, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        payload = {"model": model, "messages": self._messages(prompt), "stream": False}
        for key in ("temperature", "top_p", "max_tokens"):
            if key in kwargs:
                payload[key] = kwargs[key]
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
            response = await client.post(f"{self.endpoint}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "response": text,
            "usage_metadata": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        }

    async def generate_stream(self, model: str, prompt: str, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        payload = {"model": model, "messages": self._messages(prompt), "stream": True}
        for key in ("temperature", "top_p", "max_tokens"):
            if key in kwargs:
                payload[key] = kwargs[key]
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
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
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                        yield {"response": delta, "done": False}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def generate_rag_answer(
        self, query: str, chunks: List[Dict[str, Any]], model: str, use_structured_output: bool = False
    ) -> Dict[str, Any]:
        prompt = self.prompt_builder.create_rag_prompt(query, chunks)
        response = await self.generate(model=model, prompt=prompt, temperature=0.7, top_p=0.9)
        answer_text = response["response"] if response else ""
        return {
            "answer": answer_text,
            "sources": build_sources(chunks),
            "confidence": "medium",
            "citations": build_citations(chunks),
        }

    async def generate_rag_answer_stream(
        self, query: str, chunks: List[Dict[str, Any]], model: str
    ) -> AsyncIterator[Dict[str, Any]]:
        prompt = self.prompt_builder.create_rag_prompt(query, chunks)
        async for chunk in self.generate_stream(model=model, prompt=prompt, temperature=0.7, top_p=0.9):
            yield chunk
