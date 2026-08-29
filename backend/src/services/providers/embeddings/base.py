from abc import ABC, abstractmethod
from typing import List


class EmbeddingProvider(ABC):
    """Abstract interface for embedding providers.

    All embedding providers expose the same async interface so that the
    retrieval and indexing engines remain independent of how (or where)
    embeddings are computed. Implementations may run fully locally
    (HuggingFace), against a local inference server (Ollama), or against any
    OpenAI-compatible embeddings endpoint.
    """

    #: Human-readable identifier stored alongside indexed vectors.
    model_name: str = "unknown"
    #: Embedding vector dimension. Must match the OpenSearch vector mapping.
    dimension: int = 0

    @abstractmethod
    async def embed_passages(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """Embed a list of passages for indexing.

        :param texts: Passages to embed.
        :param batch_size: Number of passages per underlying request/batch.
        :returns: One embedding vector per input passage, order preserved.
        """
        raise NotImplementedError

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        """Embed a single search query.

        :param query: Query text to embed.
        :returns: The query embedding vector.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release any underlying resources (network clients, models)."""
        return None

    async def __aenter__(self) -> "EmbeddingProvider":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
