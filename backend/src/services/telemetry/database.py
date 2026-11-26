"""Database calls: each statement an engine runs is a call to ``postgres``, timed and counted by outcome.

Done with the engine's own events, so every repository, whoever wrote it, is covered. Nothing about the statement
(its text or its parameters, which could hold a question or a name) is recorded. The listeners run inside the
database call itself, so, as everywhere in telemetry, whatever goes wrong in them is dropped.
"""

import functools
import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from .telemetry import current_telemetry

logger = logging.getLogger(__name__)

DEPENDENCY = "postgres"
_CALLS = "rag_telemetry_calls"


def _fail_open(listener):
    @functools.wraps(listener)
    def guarded(*args: Any) -> None:
        try:
            listener(*args)
        except Exception:  # noqa: BLE001
            logger.debug("Telemetry dropped a database call", exc_info=True)

    return guarded


def instrument_engine(engine: Engine) -> None:
    """Time and count every statement ``engine`` runs, as a call under whatever span is current."""

    @event.listens_for(engine, "before_cursor_execute")
    @_fail_open
    def started(connection: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        connection.info.setdefault(_CALLS, {})[id(context)] = current_telemetry().begin_dependency(DEPENDENCY)

    @event.listens_for(engine, "after_cursor_execute")
    @_fail_open
    def finished(connection: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        call = connection.info.get(_CALLS, {}).pop(id(context), None)
        if call is not None:
            call.end()

    @event.listens_for(engine, "handle_error")
    @_fail_open
    def failed(exception_context: Any) -> None:
        connection = exception_context.connection
        call = connection.info.get(_CALLS, {}).pop(id(exception_context.execution_context), None) if connection else None
        if call is None and connection is None:
            # The database could not be reached, so no statement began: still a failed call.
            call = current_telemetry().begin_dependency(DEPENDENCY)
        if call is not None:
            call.end(error=True)
