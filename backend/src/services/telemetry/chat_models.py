"""Telemetry for LangChain chat models, which the agentic graph's nodes call instead of the provider's own methods.

A callback sees each model call start and end, so it is timed and counted as a call to the ``chat`` dependency, and
the usage the model reports is counted as tokens. Prompts and answers pass through the callback; it reads neither.
"""

from typing import Any, Dict, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from .telemetry import current_telemetry


class ChatTelemetryCallback(AsyncCallbackHandler):
    # Run in the caller's task, so the call's span becomes a child of the stage that made it.
    run_inline = True

    def __init__(self, model: Optional[str]):
        self._model = model
        self._calls: Dict[UUID, Any] = {}

    async def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._calls[run_id] = current_telemetry().begin_dependency("chat")

    async def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._calls.setdefault(run_id, current_telemetry().begin_dependency("chat"))

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._calls.pop(run_id, None)
        if call is not None:
            call.end()
        usage = _reported_usage(response)
        if usage:
            current_telemetry().record_tokens(self._model, usage.get("input_tokens"), usage.get("output_tokens"))

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._calls.pop(run_id, None)
        if call is not None:
            call.end(error=isinstance(error, Exception), cancelled=not isinstance(error, Exception))


def _reported_usage(response: Any) -> Dict[str, Any]:
    """The usage the model reported for this call; empty when it reported none (it is never estimated)."""
    try:
        message = response.generations[0][0].message
        return dict(message.usage_metadata or {})
    except (AttributeError, IndexError, TypeError):
        return {}
