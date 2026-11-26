"""The metrics the platform defines: one table the code, the dashboards and the alert rules all draw on.

Names are OpenTelemetry style (dots); Prometheus shows them with underscores and a unit suffix, for example
``rag.http.request.duration`` as ``rag_http_request_duration_seconds``.
"""

from dataclasses import dataclass
from typing import Tuple

COUNTER = "counter"
HISTOGRAM = "histogram"

DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
# Ingestion stages and Connector runs take minutes, not milliseconds.
LONG_DURATION_BUCKETS = (0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0)


# A model label comes from the request on the legacy routes, so how many distinct ones are kept is bounded.
MAX_MODEL_LABELS = 50
MAX_MODEL_LABEL_LENGTH = 64


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str
    unit: str
    description: str
    labels: Tuple[str, ...]
    buckets: Tuple[float, ...] = DURATION_BUCKETS

    @property
    def prometheus_name(self) -> str:
        """The base name Prometheus shows: dots as underscores, ``_seconds`` for a time, ``_total`` for a counter.

        A histogram is queried as this name plus ``_bucket``, ``_sum`` or ``_count``.
        """
        name = self.name.replace(".", "_")
        if self.unit == "s":
            name += "_seconds"
        if self.kind == COUNTER:
            name += "_total"
        return name

    @property
    def prometheus_series(self) -> Tuple[str, ...]:
        """Every series name the metric exposes, as a dashboard or an alert rule may query it."""
        if self.kind == HISTOGRAM:
            return tuple(f"{self.prometheus_name}{suffix}" for suffix in ("_bucket", "_sum", "_count"))
        return (self.prometheus_name,)


HTTP_REQUESTS = "rag.http.requests"
HTTP_REQUEST_ERRORS = "rag.http.request.errors"
HTTP_REQUEST_DURATION = "rag.http.request.duration"
RETRIEVAL_SEARCHES = "rag.retrieval.searches"
RETRIEVAL_CHUNKS = "rag.retrieval.chunks_returned"
RETRIEVAL_EMPTY = "rag.retrieval.empty_results"
RETRIEVAL_RERANK_SKIPPED = "rag.retrieval.rerank_skipped"
GUARDRAIL_OUT_OF_SCOPE = "rag.guardrail.out_of_scope"
CACHE_LOOKUPS = "rag.cache.lookups"
TIME_TO_FIRST_TOKEN = "rag.answer.time_to_first_token"
DEPENDENCY_CALLS = "rag.dependency.calls"
DEPENDENCY_DURATION = "rag.dependency.duration"
TOKENS_IN = "rag.llm.tokens.input"
TOKENS_OUT = "rag.llm.tokens.output"
INGESTION_STAGE_DURATION = "rag.ingestion.stage.duration"
INGESTION_QUEUE_WAIT = "rag.ingestion.queue_wait"
CONNECTOR_SYNC_DURATION = "rag.connector.sync.duration"
CONNECTOR_DOCUMENTS = "rag.connector.documents"

CATALOGUE: Tuple[MetricSpec, ...] = (
    MetricSpec(HTTP_REQUESTS, COUNTER, "{request}", "API requests handled.", ("route", "method", "status", "application")),
    MetricSpec(
        HTTP_REQUEST_ERRORS,
        COUNTER,
        "{request}",
        "API requests that ended in a server error (status 500 or above).",
        ("route", "method", "status", "application"),
    ),
    MetricSpec(
        HTTP_REQUEST_DURATION,
        HISTOGRAM,
        "s",
        "Time to handle an API request; for a streamed answer, until the last byte is sent.",
        ("route", "method", "status", "application"),
    ),
    MetricSpec(
        RETRIEVAL_SEARCHES,
        COUNTER,
        "{search}",
        "Knowledge Base searches. A request over two Knowledge Bases adds one per Knowledge Base.",
        ("knowledge_base",),
    ),
    MetricSpec(
        RETRIEVAL_CHUNKS,
        COUNTER,
        "{chunk}",
        "Chunks returned from a Knowledge Base, after reranking and the cut to the requested count.",
        ("knowledge_base",),
    ),
    MetricSpec(
        RETRIEVAL_EMPTY, COUNTER, "{search}", "Knowledge Base searches that matched no chunk at all.", ("knowledge_base",)
    ),
    MetricSpec(
        RETRIEVAL_RERANK_SKIPPED,
        COUNTER,
        "{search}",
        "Searches whose reranker was configured but failed, so the hits kept their original order.",
        ("knowledge_base",),
    ),
    MetricSpec(
        GUARDRAIL_OUT_OF_SCOPE,
        COUNTER,
        "{question}",
        "Questions the guardrail refused as outside the Application's scope.",
        ("application",),
    ),
    MetricSpec(CACHE_LOOKUPS, COUNTER, "{lookup}", "Answer-cache lookups, by outcome (hit or miss).", ("outcome",)),
    MetricSpec(
        TIME_TO_FIRST_TOKEN,
        HISTOGRAM,
        "s",
        "Time from the request arriving to the first token of an answer streamed token by token (not a refusal or an answer produced whole).",
        ("application", "model"),
    ),
    MetricSpec(
        DEPENDENCY_CALLS,
        COUNTER,
        "{call}",
        "Calls to a dependency (postgres, opensearch, redis, chat, embedding, rerank), by outcome (ok, error or cancelled).",
        ("dependency", "outcome"),
    ),
    MetricSpec(
        DEPENDENCY_DURATION,
        HISTOGRAM,
        "s",
        "Time a dependency took to answer a call; for a streamed chat answer, until the stream ended.",
        ("dependency", "outcome"),
    ),
    MetricSpec(
        TOKENS_IN,
        COUNTER,
        "{token}",
        "Prompt tokens the model reported using. Calls whose backend reports no usage add nothing; nothing is estimated.",
        ("application", "model"),
    ),
    MetricSpec(
        TOKENS_OUT,
        COUNTER,
        "{token}",
        "Completion tokens the model reported producing. Calls whose backend reports no usage add nothing.",
        ("application", "model"),
    ),
    MetricSpec(
        INGESTION_STAGE_DURATION,
        HISTOGRAM,
        "s",
        "Time one ingestion stage (fetch, parse, chunk, embed, index) took for one Document, by outcome "
        "(succeeded, failed or interrupted). A stage that never started has no observation.",
        ("knowledge_base", "stage", "outcome"),
        LONG_DURATION_BUCKETS,
    ),
    MetricSpec(
        INGESTION_QUEUE_WAIT,
        HISTOGRAM,
        "s",
        "Time a Document waited for a free ingestion slot (INGESTION__CONCURRENCY) before its ingestion began.",
        ("knowledge_base",),
        LONG_DURATION_BUCKETS,
    ),
    MetricSpec(
        CONNECTOR_SYNC_DURATION,
        HISTOGRAM,
        "s",
        "Time one Connector run took (a dry run is not measured), by outcome (ok, partial when some files failed, "
        "or error when the run itself failed).",
        ("knowledge_base", "outcome"),
        LONG_DURATION_BUCKETS,
    ),
    MetricSpec(
        CONNECTOR_DOCUMENTS,
        COUNTER,
        "{document}",
        "Documents a Connector run changed in its Knowledge Base, by outcome (added, updated, removed or failed). "
        "Files left alone are not counted. Every outcome is reported by every run, as 0 when none.",
        ("knowledge_base", "outcome"),
    ),
)
