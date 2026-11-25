"""The metrics the platform defines: one table the code, the dashboards and the alert rules all draw on.

Names are OpenTelemetry style (dots); Prometheus shows them with underscores and a unit suffix, for example
``rag.http.request.duration`` as ``rag_http_request_duration_seconds``.
"""

from dataclasses import dataclass
from typing import Tuple

COUNTER = "counter"
HISTOGRAM = "histogram"

DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str
    unit: str
    description: str
    labels: Tuple[str, ...]


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
)
