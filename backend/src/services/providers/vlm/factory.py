import logging
from typing import Optional

from src.config import Settings, get_settings

from .base import VLMProvider
from .ollama_provider import OllamaVLMProvider
from .openai_compatible_provider import OpenAICompatibleVLMProvider

logger = logging.getLogger(__name__)


def make_vlm_provider(settings: Optional[Settings] = None) -> Optional[VLMProvider]:
    """Create the configured VLM provider, or ``None`` when disabled.

    VLM support is optional and off by default. It is only instantiated when
    ``VLM__ENABLED`` is true, keeping CPU-only and text-only deployments lean.

    :param settings: Optional settings instance.
    :returns: A concrete :class:`VLMProvider`, or ``None`` if disabled.
    """
    if settings is None:
        settings = get_settings()

    cfg = settings.vlm
    if not cfg.enabled:
        return None

    provider = cfg.provider.lower()

    if provider == "ollama":
        return OllamaVLMProvider(endpoint=cfg.endpoint, model=cfg.model)

    if provider in {"openai", "openai-compatible", "openai_compatible", "vllm", "nim"}:
        return OpenAICompatibleVLMProvider(
            endpoint=cfg.endpoint,
            model=cfg.model,
            api_key=cfg.api_key or None,
        )

    raise ValueError(f"Unsupported VLM provider: {cfg.provider!r}")
