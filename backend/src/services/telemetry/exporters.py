"""Exporters that fail open: a collector that is down, slow or erroring costs a warning, never a request.

Export already runs on background threads (the batch span processor and the periodic metric reader), so a request
never waits on it. These wrappers add what the SDK does not: nothing an exporter raises escapes, and the first
failure of an outage is logged once rather than on every attempt.
"""

import logging
import threading
from typing import Sequence

from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


class ExportHealth:
    """Whether the last export worked, shared by the span and metric exporters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failing = False

    @property
    def failing(self) -> bool:
        return self._failing

    def succeeded(self) -> None:
        with self._lock:
            if self._failing:
                logger.info("Telemetry export recovered")
            self._failing = False

    def failed(self, what: str, error: object) -> None:
        with self._lock:
            warn = not self._failing
            self._failing = True
        if warn:
            logger.warning("Telemetry %s is being dropped: the collector is unreachable or refusing it (%s)", what, error)


class FailOpenSpanExporter(SpanExporter):
    def __init__(self, inner: SpanExporter, health: ExportHealth):
        self._inner = inner
        self._health = health

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:  # noqa: BLE001
            self._health.failed("traces", exc)
            return SpanExportResult.FAILURE
        if result == SpanExportResult.SUCCESS:
            self._health.succeeded()
        else:
            self._health.failed("traces", "export failed")
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("Span exporter did not shut down cleanly", exc_info=True)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False


class FailOpenMetricExporter(MetricExporter):
    def __init__(self, inner: MetricExporter, health: ExportHealth):
        super().__init__()
        self._inner = inner
        self._health = health

    def export(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs) -> MetricExportResult:
        try:
            result = self._inner.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._health.failed("metrics", exc)
            return MetricExportResult.FAILURE
        if result == MetricExportResult.SUCCESS:
            self._health.succeeded()
        else:
            self._health.failed("metrics", "export failed")
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        try:
            self._inner.shutdown(timeout_millis=timeout_millis, **kwargs)
        except Exception:  # noqa: BLE001
            logger.debug("Metric exporter did not shut down cleanly", exc_info=True)
