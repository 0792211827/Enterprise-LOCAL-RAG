# ADR-0006: Telemetry uses OpenTelemetry and stays separate from the audit log

- **Status:** Accepted and implemented (spec `.scratch/telemetry/spec.md`; operator docs `docs/deployment/telemetry.md`). Supersedes the "defer" note in FEATURE_REGISTER 3.12 and roadmap Phase 11.
- **Context:** Operators need to see whether the platform is up, slow or erroring, how much capacity it uses (tokens, ingestion, dependency latency) and which Applications are used. The platform runs on-premises, so nothing may leave the company network. The audit log already records who did what, but it is a compliance record, not a performance tool.
- **Decision:**
  - Instrument the API and the `connector` process with the vendor-neutral OpenTelemetry SDK, exporting to a self-hosted OTel Collector, then Prometheus (metrics) and Tempo (traces), viewed in Grafana. All of it is off by default and sits in an optional Compose profile, listening on localhost and the internal network only.
  - Telemetry is **not** merged into the audit log. They share only the `request_id`; the audit event's details also carry the trace ID.
  - Telemetry fails open: a down collector never slows or refuses a request. The audit log may refuse (`AUDIT__FAIL_MODE`). The two deliberately differ.
  - Telemetry never names a person and never carries prompt, answer or document text. Request metrics are labelled by Application (slug or ID); retrieval metrics by Knowledge Base. A test enforces the no-text and no-person rule.
  - v1 covers metrics and traces. Logs stay JSON on stdout with `request_id` and trace ID. Container CPU, RAM and GPU are out of scope.
  - Defaults: all traces kept (`OTEL__TRACE_SAMPLE_RATIO=1.0`), 15 days of metrics, 3 days of traces, all configurable.
- **Considered options:** `prometheus-client` only (simpler, but no traces and no backend freedom); merging telemetry into the audit pipeline (one stream, but it would force telemetry to share the audit log's compliance guarantees and its refuse-on-failure mode).
- **Consequences:** every answer path (`services/applications/answer.py`, the legacy paths and the agentic graph nodes) gains spans and timers; new `OTEL__*` settings and a Compose profile are needed; Grafana needs a required admin password and a matching `compose.hardened.yml` entry.
