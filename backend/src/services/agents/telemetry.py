"""Telemetry for the agentic graph: one span per node that runs."""

import functools
from typing import Any, Awaitable, Callable

from src.services.telemetry import current_telemetry


def traced(stage: str, node: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """``node`` run inside a span named ``stage``.

    ``functools.wraps`` keeps the node's signature visible, which LangGraph reads to inject the runtime.
    """

    @functools.wraps(node)
    async def run(*args: Any, **kwargs: Any) -> Any:
        with current_telemetry().stage(stage):
            return await node(*args, **kwargs)

    return run


def refusing(node: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """The out-of-scope node, in a span of its own, and counting the refusal the guardrail made."""

    @functools.wraps(node)
    async def run(*args: Any, **kwargs: Any) -> Any:
        telemetry = current_telemetry()
        telemetry.record_out_of_scope()
        with telemetry.stage("out_of_scope"):
            return await node(*args, **kwargs)

    return run
