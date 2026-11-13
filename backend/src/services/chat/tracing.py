"""Tracing chat exchanges in Langfuse without their content unless the operator opts in (``CHAT__TRACE_CONTENT``)."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def trace_chat_exchange(
    tracer: Any,
    metadata: Dict[str, Any],
    question: Optional[str] = None,
    answer: Optional[str] = None,
) -> None:
    """Record one answered question. ``metadata`` is the facts about it (ids, counts, timings); ``question`` and
    ``answer`` are sent only when the caller passes them, which it does only for an opted-in operator.

    Tracing must never break chat: any failure is logged and dropped.
    """
    client = getattr(tracer, "client", None)
    if client is None:
        return
    try:
        span = client.start_span(
            name="chat_answer",
            input={"question": question} if question is not None else None,
            metadata=metadata,
        )
        if answer is not None:
            span.update(output={"answer": answer})
        span.end()
        tracer.flush()
    except Exception as exc:  # noqa: BLE001
        logger.error("Error tracing chat exchange: %s", exc)
