import logging
from typing import Optional

from src.config import Settings, get_settings

from .base import EmbeddingProvider
from .huggingface_provider import HuggingFaceEmbeddingProvider
from .ollama_provider import OllamaEmbeddingProvider
from .openai_compatible_provider import OpenAICompatibleEmbeddingProvider

logger = logging.getLogger(__name__)


def make_embedding_provider(settings: Optional[Settings] = None) -> EmbeddingProvider:
    """Create the configured embedding provider.

    Selection is driven entirely by configuration (``EMBEDDING__PROVIDER``), so
    switching between local HuggingFace, Ollama, or an OpenAI-compatible
    endpoint never requires source changes. The default is fully local.

    :param settings: Optional settings instance.
    :returns: A concrete :class:`EmbeddingProvider`.
    """
    if settings is None:
        settings = get_settings()

    cfg = settings.embedding
    provider = cfg.provider.lower()

    if provider in {"huggingface", "hf", "local", "sentence-transformers"}:
        return HuggingFaceEmbeddingProvider(
            model_name=cfg.model,
            dimension=cfg.dimension,
            device=cfg.device or None,
            normalize=cfg.normalize,
            query_prompt=cfg.query_prompt or None,
            passage_prompt=cfg.passage_prompt or None,
        )

    if provider == "ollama":
        return OllamaEmbeddingProvider(
            endpoint=cfg.endpoint,
            model_name=cfg.model,
            dimension=cfg.dimension,
        )

    if provider in {"openai", "openai-compatible", "openai_compatible", "vllm", "nim", "tei"}:
        return OpenAICompatibleEmbeddingProvider(
            endpoint=cfg.endpoint,
            model_name=cfg.model,
            dimension=cfg.dimension,
            api_key=cfg.api_key or None,
        )

    raise ValueError(f"Unsupported embedding provider: {cfg.provider!r}")
