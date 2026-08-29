from typing import Optional

from src.config import Settings
from src.services.providers.embeddings.base import EmbeddingProvider
from src.services.providers.embeddings.factory import make_embedding_provider


def make_embeddings_service(settings: Optional[Settings] = None) -> EmbeddingProvider:
    """Create the configured (local-first) embedding provider.

    Backwards-compatible entry point retained for existing call sites. The
    concrete provider is selected from configuration (``EMBEDDING__PROVIDER``).

    :param settings: Optional settings instance.
    :returns: An :class:`EmbeddingProvider` implementation.
    """
    return make_embedding_provider(settings)


def make_embeddings_client(settings: Optional[Settings] = None) -> EmbeddingProvider:
    """Alias of :func:`make_embeddings_service` for existing call sites."""
    return make_embedding_provider(settings)
