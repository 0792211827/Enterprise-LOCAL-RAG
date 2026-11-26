"""The trace a piece of work belongs to, for the places that name it: the Audit log and log lines (ADR-0006)."""

from typing import Optional

from opentelemetry import trace


def current_trace_id() -> Optional[str]:
    """The current trace's ID as 32 hex digits, or None when no trace is current (telemetry is off, or outside a request)."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
