import logging
from typing import Any, Dict, Optional

import httpx

from .base import VLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleVLMProvider(VLMProvider):
    """VLM provider for OpenAI-compatible multimodal chat endpoints."""

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

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
                response = await client.get(f"{self.endpoint}/models")
                if response.status_code == 200:
                    return {"status": "healthy", "message": "OpenAI-compatible VLM endpoint is running"}
                return {"status": "unhealthy", "message": f"Endpoint returned {response.status_code}"}
        except Exception as e:  # noqa: BLE001
            return {"status": "unhealthy", "message": str(e)}

    async def describe_image(self, image_base64: str, prompt: str, model: str = "") -> str:
        payload = {
            "model": model or self.default_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                    ],
                }
            ],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
            response = await client.post(f"{self.endpoint}/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
