import logging
from typing import Any, Dict

import httpx

from .base import VLMProvider

logger = logging.getLogger(__name__)


class OllamaVLMProvider(VLMProvider):
    """VLM provider backed by a local Ollama server (e.g. qwen2.5-vl, llava)."""

    def __init__(self, endpoint: str = "http://localhost:11434", model: str = "qwen2.5-vl", timeout: float = 300.0):
        self.endpoint = endpoint.rstrip("/")
        self.default_model = model
        self.timeout = httpx.Timeout(timeout)

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.endpoint}/api/version")
                if response.status_code == 200:
                    return {"status": "healthy", "message": "Ollama VLM endpoint is running"}
                return {"status": "unhealthy", "message": f"Endpoint returned {response.status_code}"}
        except Exception as e:  # noqa: BLE001
            return {"status": "unhealthy", "message": str(e)}

    async def describe_image(self, image_base64: str, prompt: str, model: str = "") -> str:
        payload = {
            "model": model or self.default_model,
            "prompt": prompt,
            "images": [image_base64],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.endpoint}/api/generate", json=payload)
            response.raise_for_status()
            return response.json().get("response", "")
