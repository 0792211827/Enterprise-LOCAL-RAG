import asyncio
import logging
from typing import List, Optional

from .base import EmbeddingProvider

logger = logging.getLogger(__name__)


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    """Fully local embedding provider backed by sentence-transformers.

    This is the default provider for the platform. It runs entirely inside the
    customer's infrastructure with no external API calls, satisfying the
    zero-external-dependency requirement. The model is downloaded once (or
    pre-provisioned into the image/volume) and then served locally on CPU or
    GPU.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        dimension: int = 1024,
        device: Optional[str] = None,
        normalize: bool = True,
        query_prompt: Optional[str] = None,
        passage_prompt: Optional[str] = None,
    ):
        """Initialize the local embedding provider.

        :param model_name: HuggingFace/sentence-transformers model id or path.
        :param dimension: Expected embedding dimension (must match index mapping).
        :param device: Torch device ("cpu", "cuda", "mps"); auto-detected if None.
        :param normalize: Whether to L2-normalize embeddings (recommended for cosine).
        :param query_prompt: Optional instruction prefix prepended to queries.
        :param passage_prompt: Optional instruction prefix prepended to passages.
        """
        self.model_name = model_name
        self.dimension = dimension
        self.normalize = normalize
        self.query_prompt = query_prompt
        self.passage_prompt = passage_prompt
        self._device = device
        self._model = None
        logger.info(f"HuggingFace embedding provider configured: model={model_name}, dim={dimension}")

    def _ensure_model(self):
        """Lazily load the model on first use to keep startup fast."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading local embedding model '{self.model_name}' (device={self._device or 'auto'})")
            self._model = SentenceTransformer(self.model_name, device=self._device)
            actual_dim = self._model.get_sentence_embedding_dimension()
            if actual_dim and actual_dim != self.dimension:
                logger.warning(
                    f"Configured embedding dimension ({self.dimension}) does not match model "
                    f"dimension ({actual_dim}); using model dimension."
                )
                self.dimension = actual_dim
        return self._model

    def _encode_sync(self, texts: List[str], batch_size: int, prompt: Optional[str]) -> List[List[float]]:
        model = self._ensure_model()
        inputs = [f"{prompt}{t}" for t in texts] if prompt else texts
        vectors = model.encode(
            inputs,
            batch_size=batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    async def embed_passages(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        if not texts:
            return []
        embeddings = await asyncio.to_thread(self._encode_sync, texts, batch_size, self.passage_prompt)
        logger.info(f"Embedded {len(texts)} passages locally with {self.model_name}")
        return embeddings

    async def embed_query(self, query: str) -> List[float]:
        embeddings = await asyncio.to_thread(self._encode_sync, [query], 1, self.query_prompt)
        return embeddings[0]

    async def close(self) -> None:
        self._model = None
