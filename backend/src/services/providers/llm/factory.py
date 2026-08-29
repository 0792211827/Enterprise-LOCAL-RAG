import logging
from typing import Optional

from src.config import Settings, get_settings
from src.services.ollama.client import OllamaClient

from .base import LLMProvider
from .openai_compatible_provider import OpenAICompatibleLLMProvider

logger = logging.getLogger(__name__)


def make_llm_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """Create the configured LLM provider.

    Selection is configuration-driven (``LLM__PROVIDER``). The default is a
    local Ollama server. ``OllamaClient`` implements :class:`LLMProvider`
    directly so the existing RAG orchestration keeps working unchanged.

    :param settings: Optional settings instance.
    :returns: A concrete :class:`LLMProvider`.
    """
    if settings is None:
        settings = get_settings()

    cfg = settings.llm
    provider = cfg.provider.lower()

    if provider == "ollama":
        return OllamaClient(settings)

    if provider in {"openai", "openai-compatible", "openai_compatible", "vllm", "nim", "sglang", "localai"}:
        return OpenAICompatibleLLMProvider(
            endpoint=cfg.endpoint,
            model=cfg.model,
            api_key=cfg.api_key or None,
            timeout=float(cfg.timeout),
        )

    raise ValueError(f"Unsupported LLM provider: {cfg.provider!r}")
