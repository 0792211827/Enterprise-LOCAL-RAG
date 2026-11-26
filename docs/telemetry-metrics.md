# Telemetry metrics catalogue

Every metric the platform defines. The code (`backend/src/services/telemetry/catalogue.py`), the Grafana dashboards and the alert rules draw on this one table, and a test fails if they disagree: a dashboard or alert rule that queries a metric missing from here, or a metric here the platform does not define, breaks the build.

Names are OpenTelemetry style (dots). Prometheus shows them with underscores, `_seconds` after a time, and `_total` after a counter; a histogram is queried as `_bucket`, `_sum` and `_count`. The units in braces (`{request}`) are counts and add nothing to the Prometheus name.

Prometheus adds `service_name` to every series (`rag-api` or `rag-connector`). The labels below are all there is: a name outside this set is rejected in tests and dropped in production, so no metric can carry a question, an answer, Document text, or who asked (ADR-0006).

- **`application`** is the Application's slug, which a rename leaves alone. An unknown or deleted Application is `unknown`. A request that is not for one Application has no `application` label.
- **`knowledge_base`** is the Knowledge Base's ID. An unknown one is `unknown`.
- **`route`** is the route template (`/api/v1/applications/{application_id}/ask`), never the URL, so IDs do not multiply series.
- **`model`** is the model name, with at most 50 distinct values kept (the rest are `other`).
- A caller's `X-Request-ID` is never a label; it is on the trace only.

| Metric | Prometheus series | Type | Unit | Labels | Meaning |
|--------|-------------------|------|------|--------|---------|
| `rag.http.requests` | `rag_http_requests_total` | counter | `{request}` | `route`, `method`, `status`, `application` | API requests handled. |
| `rag.http.request.errors` | `rag_http_request_errors_total` | counter | `{request}` | `route`, `method`, `status`, `application` | API requests that ended in a server error (status 500 or above). |
| `rag.http.request.duration` | `rag_http_request_duration_seconds` | histogram | `s` | `route`, `method`, `status`, `application` | Time to handle an API request; for a streamed answer, until the last byte is sent. |
| `rag.retrieval.searches` | `rag_retrieval_searches_total` | counter | `{search}` | `knowledge_base` | Knowledge Base searches. A request over two Knowledge Bases adds one per Knowledge Base. |
| `rag.retrieval.chunks_returned` | `rag_retrieval_chunks_returned_total` | counter | `{chunk}` | `knowledge_base` | Chunks returned from a Knowledge Base, after reranking and the cut to the requested count. |
| `rag.retrieval.empty_results` | `rag_retrieval_empty_results_total` | counter | `{search}` | `knowledge_base` | Knowledge Base searches that matched no chunk at all. |
| `rag.retrieval.rerank_skipped` | `rag_retrieval_rerank_skipped_total` | counter | `{search}` | `knowledge_base` | Searches whose reranker was configured but failed, so the hits kept their original order. |
| `rag.guardrail.out_of_scope` | `rag_guardrail_out_of_scope_total` | counter | `{question}` | `application` | Questions the guardrail refused as outside the Application's scope. |
| `rag.cache.lookups` | `rag_cache_lookups_total` | counter | `{lookup}` | `outcome` | Answer-cache lookups, by outcome (hit or miss). |
| `rag.answer.time_to_first_token` | `rag_answer_time_to_first_token_seconds` | histogram | `s` | `application`, `model` | Time from the request arriving to the first token of an answer streamed token by token (not a refusal or an answer produced whole). |
| `rag.dependency.calls` | `rag_dependency_calls_total` | counter | `{call}` | `dependency`, `outcome` | Calls to a dependency (postgres, opensearch, redis, chat, embedding, rerank), by outcome (ok, error or cancelled). |
| `rag.dependency.duration` | `rag_dependency_duration_seconds` | histogram | `s` | `dependency`, `outcome` | Time a dependency took to answer a call; for a streamed chat answer, until the stream ended. |
| `rag.llm.tokens.input` | `rag_llm_tokens_input_total` | counter | `{token}` | `application`, `model` | Prompt tokens the model reported using. Calls whose backend reports no usage add nothing; nothing is estimated. |
| `rag.llm.tokens.output` | `rag_llm_tokens_output_total` | counter | `{token}` | `application`, `model` | Completion tokens the model reported producing. Calls whose backend reports no usage add nothing. |
| `rag.ingestion.stage.duration` | `rag_ingestion_stage_duration_seconds` | histogram | `s` | `knowledge_base`, `stage`, `outcome` | Time one ingestion stage (fetch, parse, chunk, embed, index) took for one Document, by outcome (succeeded, failed or interrupted). A stage that never started has no observation. |
| `rag.ingestion.queue_wait` | `rag_ingestion_queue_wait_seconds` | histogram | `s` | `knowledge_base` | Time a Document waited for a free ingestion slot (INGESTION__CONCURRENCY) before its ingestion began. |
| `rag.connector.sync.duration` | `rag_connector_sync_duration_seconds` | histogram | `s` | `knowledge_base`, `outcome` | Time one Connector run took (a dry run is not measured), by outcome (ok, partial when some files failed, or error when the run itself failed). |
| `rag.connector.documents` | `rag_connector_documents_total` | counter | `{document}` | `knowledge_base`, `outcome` | Documents a Connector run changed in its Knowledge Base, by outcome (added, updated, removed or failed). Files left alone are not counted. Every outcome is reported by every run, as 0 when none. |

A counter has no series until it first counts something, so a panel for, say, `rag_cache_lookups_total` is empty until a question has been answered through the cache.

## Spans

One trace per request, named `METHOD route`, carrying the request ID as the span attribute `request_id` (search it in Tempo with `{ span.request_id = "..." }`). Its child spans are the stages of an answer: `retrieve`, `rerank`, `generate_answer` and `out_of_scope`, plus one span per agentic graph node (guardrail, retrieve, grade documents, rewrite query, generate answer). Span attributes come from the same allow-list as metric labels, and `request_id` is allowed on spans only.
