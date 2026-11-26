# Deploying telemetry

Telemetry is optional and off by default. This note is what an operator needs to know before turning it on in a real deployment; [the README](../../README.md#-telemetry) covers enabling it and reading the dashboards, and [the metrics catalogue](../telemetry-metrics.md) lists every metric.

## What runs

The Compose profile `telemetry` adds four containers: the OpenTelemetry Collector, Prometheus (metrics), Tempo (traces) and Grafana. The API and the `connector` send OTLP/HTTP to the Collector at `otel-collector:4318` (`OTEL_COLLECTOR_ENDPOINT` points the containers at a different collector). Nothing is sent outside your network. Plain `docker compose up` and `make start` start none of them.

## Localhost-only binding

In the default stack every port is bound to `127.0.0.1`:

| Service | Host port | Setting |
|---------|-----------|---------|
| Grafana | `127.0.0.1:3002` | `GRAFANA_PORT` |
| Prometheus | `127.0.0.1:9090` | `PROMETHEUS_PORT` |
| OpenTelemetry Collector (OTLP/HTTP, for a backend run on the host with `make app`) | `127.0.0.1:4318` | none |
| Tempo | not published | |

To reach Grafana from other machines, put a reverse proxy in front of it rather than widening the binding. `make telemetry-start-hardened` (the `compose.hardened.yml` overlay) publishes nothing but Grafana, on `RAG_BIND_ADDRESS` (loopback unless you set it, as for the API), keeping the same password and no-anonymous-access settings.

Prometheus and Tempo have no authentication of their own. They are reachable only on `rag-network` (and, for Prometheus, on loopback), the same trust boundary as OpenSearch and Redis; see "Production hardening" in the README.

## The Grafana password is required

`GRAFANA_ADMIN_PASSWORD` has no default. Grafana normally falls back to the password `admin`, so the `grafana` service refuses to start while the variable is empty or unset: its container exits with `GRAFANA_ADMIN_PASSWORD is not set in .env`. Set a long, unique password in `.env`. Anonymous access and self sign-up are off, and Grafana's own analytics and update checks are disabled. Single sign-on for Grafana is not provided.

## Telemetry fails open

A Collector that is down, slow or refusing data never slows, blocks or fails an answer:

- Export runs on background threads, from a bounded in-memory queue (`OTEL__QUEUE_SIZE`, 2048 spans by default). When the queue is full or an export fails, data is dropped, so a long outage cannot exhaust the API's memory.
- One warning is logged when data starts being dropped, and one line when export recovers, not one per attempt.
- The **System** page shows *Telemetry* as `failing` while it happens. That does not make the platform report `degraded`.
- You lose the numbers for the outage; nothing is replayed afterwards.

This is the opposite of the Audit log, whose `AUDIT__FAIL_MODE=deny` can refuse a request it cannot record. The two are independent (ADR-0006).

## Disk and retention

Metrics are kept 15 days (`PROMETHEUS_RETENTION`) and traces 3 days (`TEMPO_RETENTION`, a duration in hours, such as `72h`). Data is in the named volumes `prometheus_data`, `tempo_data` and `grafana_data`, which `make telemetry-stop` keeps. Lower `OTEL__TRACE_SAMPLE_RATIO` on a busy deployment to keep fewer traces; metrics are never sampled.

## Privacy

Metrics and traces never carry a question, an answer, Document or Attachment text, or an End user's subject, group or role; who asked stays in the Audit log. Applications and Knowledge Bases appear by slug or ID, never by display name. Grafana's own users are separate from the platform's identity provider.

## Not included

A log store (logs stay JSON on stdout, carrying the request ID and trace ID), container, host and GPU metrics, server exporters for PostgreSQL, OpenSearch and Redis, high availability, long-term metric storage, and a shipped alert contact point.
