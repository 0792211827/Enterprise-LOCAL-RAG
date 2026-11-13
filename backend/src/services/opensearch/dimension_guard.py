"""Detects an existing chunk index whose vector dimension disagrees with configuration.

Writing vectors of the wrong size makes every bulk request fail one chunk at a
time, so ingestion is refused up front instead. Search keeps working: it only
reads, and the query vector failing to match is visible immediately.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def check_index_dimension(opensearch: Any, configured: int) -> Optional[str]:
    """The refusal message for ingestion, or None when the index agrees with configuration.

    An absent index or an unreachable cluster sets no guard: the former is created
    with the configured dimension, the latter fails ingestion on its own.
    """
    try:
        existing = opensearch.get_embedding_dimension()
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a cluster hiccup
        logger.warning("Could not read the chunk index dimension: %s", exc)
        return None

    if not isinstance(existing, int) or existing == configured:
        return None

    message = (
        f"Ingestion is disabled: the chunk index '{opensearch.index_name}' holds {existing}-dimension vectors but "
        f"EMBEDDING__DIMENSION is {configured}. A reindex is required. Until the reindex command is available, either "
        f"set EMBEDDING__DIMENSION back to {existing}, or delete the index and re-ingest every document."
    )
    logger.error(message)
    return message
