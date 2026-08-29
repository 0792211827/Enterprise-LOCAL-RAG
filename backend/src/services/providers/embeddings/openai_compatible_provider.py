import logging
from typing import List, Optional

import httpx

from .base import EmbeddingProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embedding provider for any OpenAI-compatible ``/embeddings`` endpoint.

    Works with local inference servers that expose the OpenAI embeddings API
    (for example vLLM, LocalAI, Infinity, TEI with an OpenAI shim, or NVIDIA
    NIM). Authentication is optional; an API key is only sent when configured.
    """

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        dimension: int = 1024,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.dimension = dimension
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)
        logger.info(f"OpenAI-compatible embedding provider configured: endpoint={self.endpoint}, model={model_name}")

    async def _embed(self, inputs: List[str]) -> List[List[float]]:
        response = await self._client.post(
            f"{self.endpoint}/embeddings",
            json={"model": self.model_name, "input": inputs},
        )
        response.raise_for_status()
        data = response.json()
        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]

    async def embed_passages(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        if not texts:
            return []
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings.extend(await self._embed(batch))
        logger.info(f"Embedded {len(texts)} passages via OpenAI-compatible endpoint ({self.model_name})")
        return embeddings

    async def embed_query(self, query: str) -> List[float]:
        embeddings = await self._embed([query])
        return embeddings[0]

    async def close(self) -> None:
        await self._client.aclose()
