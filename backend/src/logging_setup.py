"""Log lines that can be followed back to a request.

Every log record carries the ID of the request being handled and the trace it belongs to (``request_id`` and
``trace_id``, None outside a request, and ``trace_id`` None while telemetry is off). With telemetry on the lines
are JSON on stdout, one object per line, so those IDs are fields a log search can use; with it off the format is
the plain text it has always been (ADR-0006).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from src.middleware.request_context import current_request_id
from src.services.telemetry import current_trace_id

TEXT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, the request and trace IDs, and any exception."""

    def format(self, record: logging.LogRecord) -> str:
        line: Dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "trace_id": getattr(record, "trace_id", None),
        }
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


_installed = False


def _install_record_factory() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.request_id = current_request_id.get()
        try:
            record.trace_id = current_trace_id()
        except Exception:  # noqa: BLE001 - a log line is never worth an error
            record.trace_id = None
        return record

    logging.setLogRecordFactory(factory)


def configure_logging(json_lines: bool, level: int = logging.INFO) -> None:
    """Stamp every record with its request and trace IDs, and set the root handler's format (JSON or plain text)."""
    _install_record_factory()
    if json_lines:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(level=level, format=TEXT_FORMAT)
