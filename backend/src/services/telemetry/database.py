"""Database calls: each statement an engine runs is a call to ``postgres``, timed and counted by outcome.

Done with the engine's own events, so every repository, whoever wrote it, is covered. Nothing about the statement
(its text or its parameters, which could hold a question or a name) is recorded.
"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from .telemetry import current_telemetry

DEPENDENCY = "postgres"
_CALLS = "rag_telemetry_calls"


def instrument_engine(engine: Engine) -> None:
    """Time and count every statement ``engine`` runs, as a call under whatever span is current."""

    @event.listens_for(engine, "before_cursor_execute")
    def started(connection: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        connection.info.setdefault(_CALLS, {})[id(context)] = current_telemetry().begin_dependency(DEPENDENCY)

    @event.listens_for(engine, "after_cursor_execute")
    def finished(connection: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        call = connection.info.get(_CALLS, {}).pop(id(context), None)
        if call is not None:
            call.end()

    @event.listens_for(engine, "handle_error")
    def failed(exception_context: Any) -> None:
        connection = exception_context.connection
        call = connection.info.get(_CALLS, {}).pop(id(exception_context.execution_context), None) if connection else None
        if call is None:
            # The statement never began (the database could not be reached): still a failed call.
            call = current_telemetry().begin_dependency(DEPENDENCY)
        call.end(error=True)
