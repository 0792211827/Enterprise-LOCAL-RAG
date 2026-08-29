import logging
from typing import List

import httpx

from .base import EmbeddingProvider

logger = logging.getLogger(__name__)


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embedding provider backed by a local Ollama server.

    Uses Ollama's native ``/api/embed`` endpoint. The Ollama server runs inside
    the customer's infrastructure, so no data leaves the deployment.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        model_name: str = "bge-m3",
        dimension: int = 1024,
        timeout: float = 60.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.dimension = dimension
        self._client = httpx.AsyncClient(timeout=timeout)
        logger.info(f"Ollama embedding provider configured: endpoint={self.endpoint}, model={model_name}")

    async def _embed(self, inputs: List[str]) -> List[List[float]]:
        response = await self._client.post(
            f"{self.endpoint}/api/embed",
            json={"model": self.model_name, "input": inputs},
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings")
        if embeddings is None and "embedding" in data:
            embeddings = [data["embedding"]]
        if not embeddings:
            raise ValueError(f"Ollama returned no embeddings for model {self.model_name}")
        return embeddings

    async def embed_passages(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        if not texts:
            return []
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings.extend(await self._embed(batch))
        logger.info(f"Embedded {len(texts)} passages via Ollama ({self.model_name})")
        return embeddings

    async def embed_query(self, query: str) -> List[float]:
        embeddings = await self._embed([query])
        return embeddings[0]

    async def close(self) -> None:
        await self._client.aclose()
