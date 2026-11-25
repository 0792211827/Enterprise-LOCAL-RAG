"""The allow-list of label and attribute names telemetry accepts (ADR-0006).

Nothing that is free text, or names a person, can be passed: a name outside these sets is rejected in tests
(``strict``) and dropped in production. The request ID is allowed on spans only, because a caller chooses it and
as a metric label it would let a client flood the metrics store with unique series.
"""

from typing import Any, Dict, Mapping, Optional

METRIC_LABELS = frozenset(
    {"route", "method", "status", "outcome", "application", "knowledge_base", "model", "stage", "dependency"}
)
SPAN_ATTRIBUTES = METRIC_LABELS | {"request_id"}

# The value for an Application or Knowledge Base that is unknown or deleted, so an arbitrary value from a caller
# can never become a series of its own.
UNKNOWN = "unknown"

MAX_VALUE_LENGTH = 128


class TelemetryLabelError(ValueError):
    """A label or attribute name outside the allow-list."""


def clean(attributes: Mapping[str, Any], allowed: frozenset[str], *, strict: bool) -> Dict[str, str]:
    """``attributes`` restricted to ``allowed`` names, with values as short strings.

    :raises TelemetryLabelError: for a name outside ``allowed``, when ``strict``; otherwise it is dropped
    """
    cleaned: Dict[str, str] = {}
    for name, value in attributes.items():
        if name not in allowed:
            if strict:
                raise TelemetryLabelError(f"{name!r} is not an allowed telemetry label")
            continue
        if value is not None:
            cleaned[name] = str(value)[:MAX_VALUE_LENGTH]
    return cleaned


def application_label(application: Any) -> str:
    """The label for an Application: its slug, which a rename leaves alone, or ``unknown``."""
    return getattr(application, "slug", None) or UNKNOWN


def knowledge_base_label(knowledge_base_id: Optional[Any]) -> str:
    return str(knowledge_base_id) if knowledge_base_id else UNKNOWN
