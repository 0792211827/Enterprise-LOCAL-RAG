import logging
from typing import Optional

from src.config import Settings, get_settings
from src.services.ollama.client import OllamaClient

from .base import LLMProvider
from .openai_compatible_provider import OpenAICompatibleLLMProvider
from .registry import ProviderConfigurationError, ProviderRegistry

logger = logging.getLogger(__name__)

OPENAI_COMPATIBLE_TYPES = {"openai", "openai-compatible", "openai_compatible", "vllm", "nim", "sglang", "localai"}


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

    if provider in OPENAI_COMPATIBLE_TYPES:
        return OpenAICompatibleLLMProvider(
            endpoint=cfg.endpoint,
            model=cfg.model,
            api_key=cfg.api_key or None,
            timeout=float(cfg.timeout),
            max_tokens=cfg.max_tokens,
            structured_output_method=cfg.structured_output_method,
        )

    raise ValueError(f"Unsupported LLM provider: {cfg.provider!r}")


def make_provider_registry(settings: Optional[Settings] = None) -> ProviderRegistry:
    """Create the registry that routes Applications to their own inference endpoints."""
    if settings is None:
        settings = get_settings()

    def build(provider_type: str, endpoint: str, api_key: Optional[str], model: str) -> LLMProvider:
        return build_endpoint_provider(provider_type, endpoint, api_key, model, settings)

    return ProviderRegistry(
        builder=build,
        default_model=settings.default_llm_model,
        allowed_hosts=settings.llm.allowed_endpoint_host_list,
    )


def build_endpoint_provider(
    provider_type: str,
    endpoint: str,
    api_key: Optional[str],
    model: str,
    settings: Optional[Settings] = None,
) -> LLMProvider:
    """A provider for one Application's endpoint, with the global timeouts and limits."""
    if settings is None:
        settings = get_settings()

    kind = provider_type.lower()
    if kind == "ollama":
        # Settings are frozen; model_copy is the supported way to override.
        return OllamaClient(settings.model_copy(update={"ollama_host": endpoint}))
    if kind in OPENAI_COMPATIBLE_TYPES:
        cfg = settings.llm
        return OpenAICompatibleLLMProvider(
            endpoint=endpoint,
            model=model or settings.default_llm_model,
            api_key=api_key,
            timeout=float(cfg.timeout),
            max_tokens=cfg.max_tokens,
            structured_output_method=cfg.structured_output_method,
        )
    raise ProviderConfigurationError(f"Unsupported LLM provider type for an application endpoint: {provider_type!r}")
