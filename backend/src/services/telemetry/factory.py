"""Build telemetry from settings, or an in-memory one for tests."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from src.config import Settings

from .exporters import ExportHealth, FailOpenMetricExporter, FailOpenSpanExporter
from .telemetry import NOOP_TELEMETRY, OtelTelemetry, Telemetry

logger = logging.getLogger(__name__)

METRIC_EXPORT_INTERVAL_MS = 15_000
MAX_EXPORT_BATCH = 512


def make_telemetry(settings: Settings) -> Telemetry:
    """The telemetry the settings ask for: no-op unless ``OTEL__ENABLED``, otherwise exporting over OTLP/HTTP.

    Export is on background threads with a bounded queue, and fails open: see ``exporters``.
    """
    config = settings.otel
    if not config.enabled:
        return NOOP_TELEMETRY

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    base = config.endpoint.rstrip("/")
    timeout_ms = int(config.export_timeout * 1000)
    health = ExportHealth()
    resource = Resource.create({"service.name": config.service_name, "service.version": settings.app_version})

    tracer_provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(config.trace_sample_ratio)))
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            FailOpenSpanExporter(OTLPSpanExporter(endpoint=f"{base}/v1/traces", timeout=config.export_timeout), health),
            max_queue_size=config.queue_size,
            max_export_batch_size=min(config.queue_size, MAX_EXPORT_BATCH),
            export_timeout_millis=timeout_ms,
        )
    )
    reader = PeriodicExportingMetricReader(
        FailOpenMetricExporter(OTLPMetricExporter(endpoint=f"{base}/v1/metrics", timeout=config.export_timeout), health),
        export_interval_millis=METRIC_EXPORT_INTERVAL_MS,
        export_timeout_millis=timeout_ms,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    logger.info("Telemetry: exporting to %s as %s", base, config.service_name)
    return OtelTelemetry(tracer_provider, meter_provider, health=health)


class InMemoryTelemetry(OtelTelemetry):
    """Real telemetry that keeps what it emits in memory, for tests to read. Rejects a label outside the allow-list."""

    def __init__(self, span_exporter: Optional[SpanExporter] = None, strict: bool = True):
        """:param span_exporter: one more exporter, wrapped and batched as in production (to be made to fail)"""
        self.metric_reader = InMemoryMetricReader()
        self.span_exporter = InMemorySpanExporter()
        health = ExportHealth()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.span_exporter))
        if span_exporter is not None:
            tracer_provider.add_span_processor(BatchSpanProcessor(FailOpenSpanExporter(span_exporter, health)))
        super().__init__(tracer_provider, MeterProvider(metric_readers=[self.metric_reader]), health=health, strict=strict)

    def points(self, name: str) -> List[Tuple[Dict[str, Any], float]]:
        """``(labels, value)`` per series of a metric; a histogram's value is its observation count."""
        data = self.metric_reader.get_metrics_data()
        found = []
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        for point in metric.data.data_points:
                            found.append((dict(point.attributes), getattr(point, "value", None) or getattr(point, "count", 0)))
        return found

    def value(self, name: str, **labels: Any) -> float:
        """The sum over the series of a metric whose labels include ``labels``."""
        return sum(value for attributes, value in self.points(name) if labels.items() <= attributes.items())

    def total(self, name: str, **labels: Any) -> float:
        """The sum of the observed values of a histogram's series whose labels include ``labels``."""
        data = self.metric_reader.get_metrics_data()
        total = 0.0
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        total += sum(p.sum for p in metric.data.data_points if labels.items() <= dict(p.attributes).items())
        return total

    def spans(self) -> List[ReadableSpan]:
        return list(self.span_exporter.get_finished_spans())

    def span_named(self, name: str) -> ReadableSpan:
        matching = [span for span in self.spans() if span.name == name]
        assert len(matching) == 1, f"expected one span named {name!r}, found {[s.name for s in self.spans()]}"
        return matching[0]

    def labels_and_attributes(self) -> List[Tuple[str, str, Any]]:
        """Every ``(where, name, value)`` emitted: metric labels and span attributes, for the privacy test."""
        emitted: List[Tuple[str, str, Any]] = []
        data = self.metric_reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    for point in metric.data.data_points:
                        emitted += [(f"metric {metric.name}", k, v) for k, v in point.attributes.items()]
        for span in self.spans():
            emitted += [(f"span {span.name}", k, v) for k, v in (span.attributes or {}).items()]
            emitted += [(f"span {span.name}", "name", span.name)]
        return emitted


def make_in_memory_telemetry() -> InMemoryTelemetry:
    return InMemoryTelemetry()
