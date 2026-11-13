"""Chooses the chat Inference endpoint for an Application.

An Application whose model configuration names an endpoint is answered by a
provider built for that endpoint; every other Application keeps using the
global provider. Providers are cached per endpoint type, address and key
fingerprint, so a rotated key (read from the environment at use time) yields a
fresh provider while unchanged configuration reuses connections.
"""

import hashlib
import os
import threading
from typing import Any, Callable, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .base import LLMProvider

ENV_REFERENCE_PREFIX = "env:"

# (provider_type, endpoint, resolved_api_key, model) -> provider
ProviderBuilder = Callable[[str, str, Optional[str], str], LLMProvider]


class ProviderConfigurationError(ValueError):
    """An Application's inference endpoint cannot be used as configured."""


class EndpointNotAllowedError(ProviderConfigurationError):
    """An endpoint's host is not on the operator's allowlist."""


def check_endpoint_allowed(endpoint: str, allowed_hosts: Sequence[str]) -> None:
    """Raise unless ``endpoint``'s host is allowed; an empty allowlist allows any host.

    An allowlist entry is a bare host (any port) or ``host:port`` (that port only).
    Matching is exact, never by suffix, so ``gpu.internal.evil.example`` does not
    match ``gpu.internal``.
    """
    if not allowed_hosts:
        return
    allowed = {host.lower() for host in allowed_hosts}
    parts = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if host in allowed or (port is not None and f"{host}:{port}" in allowed):
        return
    raise EndpointNotAllowedError(
        f"The inference endpoint host '{host or endpoint}' is not allowed. "
        f"The operator restricts endpoints with LLM__ALLOWED_ENDPOINT_HOSTS (allowed: {', '.join(sorted(allowed))})."
    )


def resolve_api_key(reference: Optional[str]) -> Optional[str]:
    """The API key a stored reference stands for.

    ``env:NAME`` is read from the environment *now*, so secrets never sit in the
    database and rotation needs no edit. Any other value is used literally.
    """
    if not reference:
        return None
    if not reference.startswith(ENV_REFERENCE_PREFIX):
        return reference
    name = reference[len(ENV_REFERENCE_PREFIX) :].strip()
    value = os.environ.get(name) if name else None
    if not value:
        raise ProviderConfigurationError(
            f"The API key for this application's inference endpoint is set to '{reference}', "
            f"but the environment variable {name or '(empty name)'} is not set on the server."
        )
    return value


def _fingerprint(api_key: Optional[str]) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else ""


class ProviderRegistry:
    """Resolves and caches the provider that serves an Application."""

    def __init__(
        self,
        builder: Optional[ProviderBuilder] = None,
        default_model: str = "",
        allowed_hosts: Sequence[str] = (),
    ):
        if builder is None:
            from .factory import build_endpoint_provider

            builder = build_endpoint_provider
        self._builder = builder
        self._default_model = default_model
        self._allowed_hosts = list(allowed_hosts)
        self._providers: Dict[Tuple[str, str, str], LLMProvider] = {}
        self._lock = threading.Lock()

    def for_application(self, application: Any, default: LLMProvider) -> LLMProvider:
        """The provider for ``application``: its own endpoint if set, else ``default`` (the global provider)."""
        config = getattr(application, "model_configuration", None)
        endpoint = (getattr(config, "llm_endpoint", None) or "").strip()
        if not endpoint:
            return default
        check_endpoint_allowed(endpoint, self._allowed_hosts)
        return self.for_endpoint(
            config.llm_provider or "ollama",
            endpoint,
            resolve_api_key(getattr(config, "llm_api_key_ref", None)),
            getattr(config, "llm_model", None) or self._default_model,
        )

    def for_endpoint(self, provider_type: str, endpoint: str, api_key: Optional[str], model: str = "") -> LLMProvider:
        key = (provider_type.lower(), endpoint.rstrip("/"), _fingerprint(api_key))
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._providers[key] = self._builder(provider_type, endpoint, api_key, model)
            return provider
