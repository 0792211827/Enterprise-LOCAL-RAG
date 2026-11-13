from typing import Optional

from src.config import Settings, get_settings

from .client import HTTPReranker, Reranker


def make_reranker(settings: Optional[Settings] = None) -> Optional[Reranker]:
    """The configured reranker, or None when reranking is off (the default)."""
    if settings is None:
        settings = get_settings()

    cfg = settings.rerank
    if not cfg.enabled:
        return None
    if not cfg.endpoint:
        raise ValueError("RERANK__ENABLED is true but RERANK__ENDPOINT is empty")
    return HTTPReranker(
        endpoint=cfg.endpoint,
        model=cfg.model,
        api_key=cfg.api_key or None,
        candidates=cfg.candidates,
        timeout=cfg.timeout,
    )
