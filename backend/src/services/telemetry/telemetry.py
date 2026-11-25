"""Telemetry: metrics and traces for operators (ADR-0006).

``Telemetry`` is the interface and, as is, the no-op used when telemetry is off, so call sites never ask whether
it is on. ``OtelTelemetry`` is the real one. Neither ever raises into a caller: whatever goes wrong inside is
dropped, because telemetry must not be able to slow, block or fail a request.

Call sites that cannot be handed the object (the retrieval seam, the agent's nodes) reach it with
``current_telemetry()``; the request middleware makes the app's telemetry current for the length of a request.
"""

import functools
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from . import catalogue as m
from .exporters import ExportHealth
from .labels import (
    METRIC_LABELS,
    SPAN_ATTRIBUTES,
    UNKNOWN,
    TelemetryLabelError,
    application_label,
    clean,
    knowledge_base_label,
)

logger = logging.getLogger(__name__)

UNMATCHED_ROUTE = "unmatched"


@dataclass
class RequestScope:
    """What one request in flight shares with the code answering it."""

    telemetry: "Telemetry"
    started: float
    application: Optional[str] = None


_scope: ContextVar[Optional[RequestScope]] = ContextVar("telemetry_request_scope", default=None)


class Stage:
    """A span for one stage of an answer (guardrail, retrieve, rerank, ...). Never raises into the caller.

    As a context manager the span is current for its body, so spans started inside become its children. Started
    with ``begin_stage`` it is not made current, for a stage that spans the yields of a streamed answer.
    """

    def __init__(self, telemetry: "OtelTelemetry", name: str, attributes: Mapping[str, Any], current: bool):
        self._telemetry = telemetry
        self._current = current
        self._span: Optional[trace.Span] = None
        self._token: Any = None
        try:
            attributes = clean({"stage": name, **attributes}, SPAN_ATTRIBUTES, strict=telemetry.strict)
            self._span = telemetry.tracer.start_span(name, attributes=attributes)
            if current:
                self._token = otel_context.attach(trace.set_span_in_context(self._span))
        except TelemetryLabelError:
            if telemetry.strict:
                raise
        except Exception:  # noqa: BLE001
            telemetry.dropped("starting a span")

    def __enter__(self) -> "Stage":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.end(error=exc_type is not None)
        return False

    def end(self, error: bool = False) -> None:
        """End the span; ``error`` marks it failed (without the exception, whose text could hold a question)."""
        span, self._span = self._span, None
        if span is None:
            return
        try:
            if self._token is not None:
                otel_context.detach(self._token)
            if error:
                span.set_status(StatusCode.ERROR)
            span.end()
        except Exception:  # noqa: BLE001
            self._telemetry.dropped("ending a span")


class RequestHandle:
    """One request being measured; ``finish`` records it."""

    def __init__(
        self, telemetry: "OtelTelemetry", method: str, span: Any, scope: RequestScope, otel_token: Any, scope_token: Any
    ):
        self._telemetry = telemetry
        self._method = method
        self._span = span
        self._scope = scope
        self._otel_token = otel_token
        self._scope_token = scope_token

    def finish(self, route: Optional[str], status: int) -> None:
        telemetry = self._telemetry
        try:
            if self._otel_token is not None:
                otel_context.detach(self._otel_token)
            _scope.reset(self._scope_token)
        except Exception:  # noqa: BLE001
            telemetry.dropped("closing a request")
        route = route or UNMATCHED_ROUTE
        labels = {"route": route, "method": self._method, "status": status}
        if self._scope.application:
            labels["application"] = self._scope.application
        try:
            if self._span is not None:
                self._span.update_name(f"{self._method} {route}")
                self._span.set_attributes(clean(labels, SPAN_ATTRIBUTES, strict=telemetry.strict))
                if status >= 500:
                    self._span.set_status(StatusCode.ERROR)
                self._span.end()
        except Exception:  # noqa: BLE001
            telemetry.dropped("ending a request span")
        telemetry.request_finished(labels, time.perf_counter() - self._scope.started, status)


class Telemetry:
    """No-op telemetry: what runs with ``OTEL__ENABLED=false``. Emits nothing and costs nothing."""

    enabled = False
    strict = False

    def start_request(self, method: str, request_id: Optional[str]) -> Optional[RequestHandle]:
        """Begin measuring a request and make this telemetry current for it; None when nothing is measured."""
        return None

    def stage(self, name: str, **attributes: Any) -> Any:
        """A span for one stage of an answer, as a context manager."""
        return _NOOP_STAGE

    def begin_stage(self, name: str, **attributes: Any) -> Any:
        """A span for a stage that outlives the caller's frame (a stream); end it with ``.end()``."""
        return _NOOP_STAGE

    def observe_application(self, application: Any) -> None:
        """Label the current request's metrics with this Application (``None``: an unknown one)."""

    def record_retrieval(
        self, knowledge_base_ids: Optional[Sequence[str]], hits: Sequence[Mapping[str, Any]], rerank: Optional[str]
    ) -> None:
        """One search finished: counted once per Knowledge Base searched (``None``: an unscoped, global search)."""

    def record_out_of_scope(self) -> None:
        """The guardrail refused a question as outside the Application's scope."""

    def record_cache_lookup(self, hit: bool) -> None:
        """An answer-cache lookup, a hit or a miss."""

    def record_first_token(self, model: Optional[str]) -> None:
        """The first token of a streamed answer was produced."""

    def shutdown(self) -> None:
        """Send what is queued and stop exporting (at the end of the process)."""

    @property
    def export_state(self) -> str:
        """``disabled``, ``exporting``, or ``failing`` (the collector is not accepting what is sent)."""
        return "disabled"


class _NoopStage:
    def __enter__(self) -> "_NoopStage":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def end(self, error: bool = False) -> None:
        pass


_NOOP_STAGE = _NoopStage()
NOOP_TELEMETRY = Telemetry()


def current_telemetry() -> Telemetry:
    """The telemetry of the request being handled, or the no-op one outside a request."""
    scope = _scope.get()
    return scope.telemetry if scope is not None else NOOP_TELEMETRY


def _fail_open(method):
    """Whatever the method raises is dropped (a bad label name excepted, when strict, so tests can see it)."""

    @functools.wraps(method)
    def guarded(self: "OtelTelemetry", *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except TelemetryLabelError:
            if self.strict:
                raise
        except Exception:  # noqa: BLE001
            self.dropped(method.__name__)
        return None

    return guarded


class OtelTelemetry(Telemetry):
    enabled = True

    def __init__(self, tracer_provider: Any, meter_provider: Any, *, health: Optional[ExportHealth] = None, strict: bool = False):
        self.strict = strict
        self.health = health or ExportHealth()
        self._dropped_warned = False
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self.tracer = tracer_provider.get_tracer("rag-api")
        meter = meter_provider.get_meter("rag-api")
        self._instruments = {}
        for spec in m.CATALOGUE:
            if spec.kind == m.COUNTER:
                self._instruments[spec.name] = meter.create_counter(spec.name, unit=spec.unit, description=spec.description)
            else:
                self._instruments[spec.name] = meter.create_histogram(
                    spec.name,
                    unit=spec.unit,
                    description=spec.description,
                    explicit_bucket_boundaries_advisory=list(m.DURATION_BUCKETS),
                )

    @property
    def export_state(self) -> str:
        return "failing" if self.health.failing else "exporting"

    @_fail_open
    def shutdown(self) -> None:
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()

    def dropped(self, what: str) -> None:
        """Telemetry failed inside itself: say so once, and carry on."""
        if not self._dropped_warned:
            self._dropped_warned = True
            logger.warning("Telemetry is dropping data (%s failed); requests are unaffected", what, exc_info=True)

    def _add(self, name: str, labels: Mapping[str, Any], amount: int = 1) -> None:
        self._instruments[name].add(amount, clean(labels, METRIC_LABELS, strict=self.strict))

    def _observe(self, name: str, value: float, labels: Mapping[str, Any]) -> None:
        self._instruments[name].record(value, clean(labels, METRIC_LABELS, strict=self.strict))

    def start_request(self, method: str, request_id: Optional[str]) -> Optional[RequestHandle]:
        try:
            # The request ID is on the span only: a caller chooses it, so as a label it would mint unbounded series.
            attributes = clean({"method": method, "request_id": request_id}, SPAN_ATTRIBUTES, strict=self.strict)
            span = self.tracer.start_span(method, kind=SpanKind.SERVER, attributes=attributes)
            otel_token = otel_context.attach(trace.set_span_in_context(span))
            scope = RequestScope(telemetry=self, started=time.perf_counter())
            return RequestHandle(self, method, span, scope, otel_token, _scope.set(scope))
        except TelemetryLabelError:
            if self.strict:
                raise
        except Exception:  # noqa: BLE001
            self.dropped("starting a request")
        return None

    @_fail_open
    def request_finished(self, labels: Dict[str, Any], seconds: float, status: int) -> None:
        self._add(m.HTTP_REQUESTS, labels)
        self._observe(m.HTTP_REQUEST_DURATION, seconds, labels)
        if status >= 500:
            self._add(m.HTTP_REQUEST_ERRORS, labels)

    def stage(self, name: str, **attributes: Any) -> Stage:
        return Stage(self, name, attributes, current=True)

    def begin_stage(self, name: str, **attributes: Any) -> Stage:
        return Stage(self, name, attributes, current=False)

    @_fail_open
    def observe_application(self, application: Any) -> None:
        scope = _scope.get()
        if scope is not None:
            scope.application = application_label(application)

    @_fail_open
    def record_retrieval(
        self, knowledge_base_ids: Optional[Sequence[str]], hits: Sequence[Mapping[str, Any]], rerank: Optional[str]
    ) -> None:
        if knowledge_base_ids is None:
            chunks_by_kb = {UNKNOWN: len(hits)}
        else:
            chunks_by_kb = {knowledge_base_label(kb): 0 for kb in knowledge_base_ids}
            for hit in hits:
                label = knowledge_base_label(hit.get("knowledge_base_id"))
                if label in chunks_by_kb:
                    chunks_by_kb[label] += 1
        for knowledge_base, chunks in chunks_by_kb.items():
            labels = {"knowledge_base": knowledge_base}
            self._add(m.RETRIEVAL_SEARCHES, labels)
            self._add(m.RETRIEVAL_CHUNKS, labels, chunks)
            if chunks == 0:
                self._add(m.RETRIEVAL_EMPTY, labels)
            if rerank == "skipped":
                self._add(m.RETRIEVAL_RERANK_SKIPPED, labels)

    @_fail_open
    def record_out_of_scope(self) -> None:
        scope = _scope.get()
        self._add(m.GUARDRAIL_OUT_OF_SCOPE, {"application": scope.application if scope else None})

    @_fail_open
    def record_cache_lookup(self, hit: bool) -> None:
        self._add(m.CACHE_LOOKUPS, {"outcome": "hit" if hit else "miss"})

    @_fail_open
    def record_first_token(self, model: Optional[str]) -> None:
        scope = _scope.get()
        if scope is not None:
            self._observe(
                m.TIME_TO_FIRST_TOKEN,
                time.perf_counter() - scope.started,
                {"application": scope.application, "model": model},
            )
