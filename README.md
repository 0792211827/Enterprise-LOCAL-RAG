# Enterprise Local RAG


A self-hosted, **local-first** Enterprise RAG-as-a-Service platform. It ingests
documents, indexes them for hybrid (BM25 + vector) retrieval, and answers
questions with a local LLM — including an **agentic RAG** workflow built on
LangGraph. Everything runs locally with **no external AI APIs** required.

## ✨ Features

- **Document ingestion** — PDF parsing (Docling), section-aware chunking, and
  hybrid indexing into OpenSearch.
- **Hybrid retrieval** — BM25 keyword search + dense vector search fused with
  Reciprocal Rank Fusion (RRF).
- **RAG question answering** — standard and streaming responses with citations.
- **Agentic RAG** — LangGraph agent with query rewriting, document grading,
  guardrails, and out-of-scope handling.
- **Admin Control Plane** — Next.js 14 UI to manage knowledge bases, documents,
  applications, providers, and system health.
- **Local-first providers** — embeddings (HuggingFace / Ollama /
  OpenAI-compatible), LLM (Ollama / OpenAI-compatible), optional VLM.
- **Caching** — Redis caching of answers.
- **Access control and audit** — accepts your SSO's identity (JWT or trusted
  proxy headers), role-based routes, per-knowledge-base grants enforced at
  retrieval, and a queryable, exportable audit log. All of it is off by default.

## 🏗️ Architecture

```
                ┌─────────────────────────┐
                │  Admin Control Plane     │  Next.js 14  (:3001)
                │  (frontend/)             │
                └───────────┬─────────────┘
                            │ HTTP
                ┌───────────▼─────────────┐
                │ FastAPI backend (backend/)│  (:8000)
                │  ingest · search · RAG   │
                │  agentic RAG (LangGraph) │
                └─┬───────┬────────┬───────┘
                  │       │        │
        ┌─────────▼─┐ ┌───▼────┐ ┌─▼──────┐ ┌──────────┐
        │PostgreSQL │ │OpenSea │ │ Ollama │ │  Redis   │
        │ metadata  │ │ hybrid │ │  LLM   │ │  cache   │
        │  (:5432)  │ │(:9200) │ │(:11434)│ │ (:6379)  │
        └───────────┘ └────────┘ └────────┘ └──────────┘
```

## 🧩 Services & Ports

| Service | URL | Purpose |
|---------|-----|---------|
| **API** | http://localhost:8000 | FastAPI backend |
| **API docs** | http://localhost:8000/docs | Interactive OpenAPI UI |
| **Admin Control Plane** | http://localhost:3001 | Next.js admin frontend |
| **OpenSearch** | http://localhost:9200 | Hybrid search engine |
| **OpenSearch Dashboards** | http://localhost:5601 | Search engine UI |
| **Ollama** | http://localhost:11434 | Local LLM / embeddings runtime |
| **PostgreSQL** | localhost:5432 | Application metadata |
| **Redis** | localhost:6379 | Response cache |
| **Grafana** (optional, [Telemetry](#-telemetry)) | http://localhost:3002 | Dashboards and alerts; started only with the `telemetry` profile |

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- [`uv`](https://github.com/astral-sh/uv) (for local Python development)
- Node.js 20+ (for local frontend development)

### Run the full stack

```bash
# 1. Configure environment
cp .env.example .env        # defaults are local-first and work out of the box

# 2. Build and start all services
make start                  # docker compose up --build -d

# 3. Check health
make health
```

Pull a local model into Ollama the first time (defaults to `llama3.2:1b`):

```bash
docker exec -it rag-ollama ollama pull llama3.2:1b
```

Then open the Admin Control Plane at **http://localhost:3001** and the API docs
at **http://localhost:8000/docs**.

### Useful Make targets

```bash
make start     # build + start all services
make install   # one-time: Python + frontend deps, .env from .env.example
make app       # dev mode: datastores in Docker, backend (reload) + frontend on the host; make app-stop to free it
make infra     # just the datastores + Ollama; then make backend / make frontend in separate terminals
make pull-model  # pull OLLAMA_MODEL (or MODEL=...) into rag-ollama
make start-hardened  # same, publishing only the API and UIs, on 127.0.0.1
make stop      # stop services
make logs      # tail logs
make health    # check API / OpenSearch / Ollama
make health-hardened # the same for a hardened stack (checks from inside the network)
make test      # run backend tests (cd backend && uv run python -m pytest)
make lint      # ruff + mypy
make frontend-dev    # frontend dev server on :3001
make frontend-build  # frontend production build + type check
make audit-purge DAYS=365  # delete audit events older than 365 days (DRY_RUN=1 to count)
make uploads-cleanup DAYS=14  # remove stale uploaded files (off unless run; DRY_RUN=1 to count)
make reindex             # rebuild the chunk index on lucene / a new embedding model, then swap the alias (DRY_RUN=1 to preview)
make connector-sync NAME=hr-share  # mirror a Connector's source folder into its Knowledge Base (ALL=1, DRY_RUN=1)
make ingest-samples      # ingest data/File Examples into a scratch Knowledge Base and report each file (URLS=1, ASK=1, CLEAN=1); not part of CI
make chat-purge DAYS=30  # delete Conversations idle for 30+ days (the API also does this daily; DRY_RUN=1 to count)
make clean     # tear down + prune
```

## 🔌 API Overview

All application routes are served under `/api/v1`, except the
OpenAI-compatible endpoint, which is mounted at bare `/v1` so stock OpenAI SDK
clients work unmodified.

| Area | Route prefix |
|------|--------------|
| Health | `GET /api/v1/health` |
| Hybrid search | `/api/v1/hybrid-search` |
| Ask / Streaming RAG | `/api/v1/ask`, `/api/v1/stream` |
| Agentic RAG | `/api/v1/ask-agentic` |
| Knowledge bases | `/api/v1/knowledge-bases` |
| Documents | `/api/v1/documents`, `/api/v1/ingestion-jobs` |
| Applications | `/api/v1/applications` |
| Application API keys | `/api/v1/applications/{id}/api-keys` |
| Providers | `/api/v1/providers` |
| System | `/api/v1/system` |
| Caller identity | `GET /api/v1/auth/me` |
| Active access controls | `GET /api/v1/system/security` |
| Audit log (auditor role) | `/api/v1/audit` |
| Employee chat (querier role) | `/api/v1/chat` (applications, conversations, messages, documents) |
| Chat conversation counts (admin) | `/api/v1/chat-admin` |
| OpenAI-compatible chat | `POST /v1/chat/completions` |

Full, interactive documentation is available at `/docs`.

## 🖥️ Admin Control Plane (Frontend)

A Next.js 14 admin UI (`frontend/`) for managing knowledge bases, document
ingestion, RAG applications (with a retrieval playground), model providers
and system health, plus an audit log viewer for auditors.

It is also the employees' chat app. Each role reaches its own surface, enforced
on the server and mirrored in the UI: `querier` gets **Chat** only, `admin` the
control plane, `auditor` the audit log (a user with several roles gets each).
In Chat an employee sees only the Applications whose *every* Knowledge Base they
hold a Grant on, gets the answer streamed, and can open each citation to see the
source Document with the cited passage marked. Conversations are not stored yet.

The UI is two surfaces on shared design tokens (`frontend/src/styles/tokens.css`,
mapped in `tailwind.config.ts`, which carries no other colours): the **User UI**
(`/chat`, warm paper, reading-first, usable one-handed on a phone) and the **Admin
UI** (everything else, cool and dense, grouped Build / Operate / Review, tables
scroll inside their container on a tablet). `src/app/AppShell.tsx` picks the shell
from the caller's roles. Shared components live in `src/components/ui/`; the
specimen at `/design` shows them with the palette and type. Fonts and icons are
bundled, so nothing is fetched from a CDN.

Run with Docker (part of `make start`):

```bash
docker compose up -d frontend   # serves on http://localhost:3001
```

Local development:

```bash
cd frontend
npm install
npm run dev          # http://localhost:3001
npm run build        # production build + type check
npx playwright install chromium   # once: the browser the UI tests drive
npm run test:e2e     # builds the app (auth off and OIDC) and runs the browser tests, offline
```

The browser tests (`frontend/e2e/`) drive production builds with the API stubbed at
the HTTP boundary (`e2e/support/api-stub.ts`), so they need no running services. A
test signs in as an End user, Admin, auditor or a caller with no role. To capture
desktop, tablet and phone screenshots into `frontend/e2e/screenshots/` for design
review, run `SCREENSHOTS=1 npm run test:e2e` (the specs capture their key states as
they pass). `e2e/final-pass.spec.ts` holds the whole-app checks: landmarks, one
heading per page, no icon glyphs, no horizontal overflow, skip link, visible focus
and reduced motion.

The browser calls the API at `http://localhost:8000` (configurable via the
`NEXT_PUBLIC_API_URL` build arg in `compose.yml`). The backend enables CORS for
`localhost:3000/3001` out of the box.

## ⚙️ Configuration

All configuration is environment-driven; see `.env.example` for the full,
documented list. Key groups:

- **Embeddings** (`EMBEDDING__*`) — provider, model, dimension, device.
  `EMBEDDING__DIMENSION` is the single source of truth for the chunk index
  mapping (`OPENSEARCH__VECTOR_DIMENSION` is deprecated and only warns). If an
  existing index was built with a different dimension, startup logs an error and
  ingestion returns `409` until the index is rebuilt (`make reindex`); search and
  the rest of the API keep working. The same command moves an index built before
  the lucene engine (which filters before it searches, so a narrow scope still
  returns a full result set) onto it, and handles a change of embedding model
  (`--re-embed`). It builds `rag-documents-chunks-vN`, copies the chunks, then
  swaps the alias; it is never run implicitly, and an interrupted run resumes
  when run again. Pause ingestion while it runs. On a deployment still on a
  concrete index the old index is removed in the swap (take a snapshot first if you
  want a way back). The previous index is deleted after every swap unless you pass
  `--keep-old`: a kept index still holds chunks that are later deleted or purged
  (private Attachment chunks included), so remove it when you are done. Run the
  reindex before relying on large Attachments: on an index still on nmslib a small
  private partition is searched with the post-filtering engine and may return few
  passages.
- **LLM** (`LLM__*`) — provider, endpoint, model, timeout, max tokens. Ollama is
  the default; llama.cpp and Ollama's `/v1` are verified OpenAI-compatible
  backends, vLLM and TGI are not yet. See
  [`docs/deployment/inference-backends.md`](docs/deployment/inference-backends.md)
  for the support matrix, opt-in overlays and what each endpoint routes through.
  Per-Application endpoints (`llm_endpoint`, with an `env:NAME` key reference) need
  no setting; `LLM__ALLOWED_ENDPOINT_HOSTS` restricts which hosts they and the
  provider test / model-listing actions may call (empty = any host). See the same doc.
- **VLM** (`VLM__*`) — optional vision-language model (off by default).
- **Reranker** (`RERANK__*`) — optional reranking endpoint that reorders retrieved
  passages before answering; a failing endpoint falls back to the original order
  and is flagged in the audit log (off by default). See
  [`docs/deployment/inference-backends.md`](docs/deployment/inference-backends.md#reranking).
- **OpenSearch** (`OPENSEARCH__*`) — index and hybrid-search settings.
- **Chunking** (`CHUNKING__*`) — chunk size, overlap, section-based mode.
- **Retrieval** (`RETRIEVAL__*`) — how new Applications find passages (`MODE`, `TOP_K`, `SCORE_THRESHOLD`). Server-controlled: the admin UI and API neither show nor accept it, and a request that sends `retrieval_mode`, `top_k`, `score_threshold` or `default_top_k` gets `422`.
- **Uploads** (`INGESTION__*`) — document upload size cap (`MAX_UPLOAD_MB`, default
  50; larger uploads get `413`), the upload directory, and an opt-in retention for
  stale files (`make uploads-cleanup`). Stored files are named by content hash;
  nothing from the uploaded filename reaches the file system. Also the images
  described per Document (`MAX_IMAGES_PER_DOCUMENT`, default 200) and the host
  allowlist for web pages (`URL_ALLOWED_HOSTS`, empty = adding a URL is refused);
  see [`docs/document-ingestion.md`](docs/document-ingestion.md).
- **Redis** (`REDIS__*`).
- **Authentication** (`AUTH__*`) — see below. Off by default.
- **Audit log** (`AUDIT__*`) — see [Audit logging](#-audit-logging). Off by default.
- **Telemetry** (`OTEL__*`, plus `GRAFANA_ADMIN_PASSWORD` and the retention
  settings) — see [Telemetry](#-telemetry). Off by default.

## 🔐 Authentication

The platform sits behind your existing identity provider. It never issues
sessions, stores passwords or runs a login flow of its own: it **accepts** an
identity your SSO already established. `AUTH__MODE` selects how:

| Mode | Use when | What is checked |
|------|----------|-----------------|
| `disabled` (default) | Local development, trusted networks | Nothing — every request is an anonymous caller with all roles, exactly as before. |
| `jwt` | Your apps or gateway forward the user's access token | `Authorization: Bearer <JWT>` on every request: signature (your IdP's JWKS, or a static PEM for air-gapped sites), `iss`, `aud`, `exp`. HS*/`none` algorithms are refused. |
| `trusted_header` | An authenticating reverse proxy (oauth2-proxy, Envoy/Istio ext-authz, a corporate gateway) fronts the API | Identity headers, honoured **only** from `AUTH__TRUSTED_PROXY_CIDRS`. Make sure the API port is reachable only through that proxy. |

**Roles.** IdP role values map to three platform roles through
`AUTH__ADMIN_ROLES`, `AUTH__QUERY_ROLES` and `AUTH__AUDITOR_ROLES`. A caller who
maps to none is rejected, so a valid token for some unrelated application of
the same IdP gets nothing.

| Role | Can use |
|------|---------|
| `admin` | The control plane: knowledge bases, documents and ingestion jobs, applications (including the playground), providers, retrieval configs, API keys, `/api/v1/system/*`. Also holds `querier`, and holds `auditor` unless `AUTH__ADMIN_CAN_READ_AUDIT=false`. |
| `querier` | The employee chat (`/api/v1/chat`: the Applications they may use, streamed answers with citations, source Documents), `/api/v1/ask`, `/stream`, `/ask-agentic`, `/hybrid-search`, and `/v1/chat/completions` |
| `auditor` | The audit log |

Only `GET /api/v1/health` is public. The policy is applied once per router in
`backend/src/main.py`, and `tests/unit/test_route_policy.py` fails if a route
is added without one. Callers without a required role get `403`.

Example for Keycloak:

```bash
AUTH__MODE=jwt
AUTH__JWKS_URL=https://sso.corp.internal/realms/acme/protocol/openid-connect/certs
AUTH__ISSUER=https://sso.corp.internal/realms/acme
AUTH__AUDIENCE=rag-platform
AUTH__ROLES_CLAIM=realm_access.roles
```

Check how a token maps with `GET /api/v1/auth/me`. It returns the subject, the
mapped roles, groups, and the raw IdP role values. Failed authentication
returns a generic `401`; the specific reason (expired, wrong audience, unknown
key…) goes to the API log. An incomplete configuration stops the API at
startup instead.

Every response carries an `X-Request-ID` header, reused if the caller sent a
valid one. Quote it when reporting a problem.

> **Status:** identity intake, role enforcement, API-key enforcement,
> knowledge-base permissions and the audit log are all live (see
> `docs/FEATURE_REGISTER.md` and `docs/IMPLEMENTATION_ROADMAP.md`).

### Knowledge-base permissions

With authentication on, a caller retrieves only from knowledge bases they
have been **granted**. This applies on every path that searches:
`/api/v1/ask`, `/stream`, `/ask-agentic`, `/hybrid-search`, the playground,
and `/v1/chat/completions`. Grants name IdP claim values, so group membership
stays managed in your IdP:

| Grant type | Matches |
|------------|---------|
| `group` | a value in the caller's groups claim (`AUTH__GROUPS_CLAIM`) |
| `role` | a raw value in the caller's roles claim |
| `subject` | one specific caller |
| `everyone` | any authenticated querier |

Manage grants in the admin UI (**Knowledge Bases → Access**) or via
`GET`/`PUT /api/v1/knowledge-bases/{id}/grants`. The same panel, or
`GET /api/v1/knowledge-bases/access-check?subject=…&groups=…`, previews what a
given identity could retrieve.

How the rules combine:

- An assistant answers only from the knowledge bases that are both attached
  to it and granted to the caller. If none are, the caller is told so, and
  nothing is searched and no model is called.
- A host app calling `/v1` with only its application key gets the
  assistant's full set of knowledge bases. If it forwards an end user
  (`X-User-Authorization`), that user's grants apply.
- Admins are bound by grants like everyone else unless
  `AUTH__ADMIN_BYPASS_RETRIEVAL=true`.
- Cached answers are only reused between callers with an identical set of
  permitted knowledge bases.

**Turning it on for an existing deployment.** With the default
`AUTH__UNGRANTED_KB_ACCESS=deny`, enabling `AUTH__MODE` makes every knowledge
base unreadable until it has grants. To migrate gradually, start with
`allow`: knowledge bases that have no grants yet stay readable by all
queriers. Add grants, check them with the access preview, then switch to
`deny`.

### Document restrictions

A **Document restriction** narrows who may read one Document inside a
knowledge base, so a single sensitive file does not need its own knowledge
base. A caller reads a Document only if they hold a Grant on its knowledge
base **and** (the Document has no restrictions **or** they match at least one).
Restrictions can never widen access, and an unrestricted Document inherits the
Grants. They use the same `group` / `role` / `subject` / `everyone` vocabulary
as Grants and hide the Document from every answer, citation and search on every
path (legacy routes, the agentic graph, the playground, chat, `/v1`).

Set and clear them in the admin UI (**Knowledge Bases → a Document →
Restrict**) or via `GET`/`PUT`/`DELETE /api/v1/documents/{id}/restrictions`
(`PUT` replaces the whole set; an empty set clears it). The response warns about
any value that matches no claim value the platform has seen (named by a Grant,
configured as a role mapping, or carried by a caller in the audit log), which
is usually a typo that would hide the Document from everyone.
`GET /api/v1/documents/{id}/access-check?subject=…&groups=…&roles=…` shows
whether an identity could read a Document and why.

A change updates the already-indexed chunks in place (no re-ingestion). The
index needs the lucene engine, so older deployments run `make reindex` once
(see ADR-0001) so narrow filters still return full result sets. API-key
callers and `AUTH__MODE=disabled` are unrestricted, as for Grants, and cached
answers are never shared between callers holding different Principals (and are
dropped when a restriction changes).

### API keys on `/v1/chat/completions`

Each application gets API keys (`sk-...`). With `API_KEYS__ENFORCE=true`, a
request must present an active key **issued for that application**. A key for
a different application is rejected. With the default `false`, a valid key's
use is recorded, but requests without one are still served (as the
authenticated user when `AUTH__MODE` is on). The admin UI's Endpoint panel
shows which applies.

A host application can authenticate with its key **and** say which end user
it's acting for, by forwarding that user's IdP token. The request is then
attributed to the user, not the application:

```python
client = OpenAI(
    base_url="http://rag.internal:8000/v1",
    api_key=APP_KEY,
    default_headers={"X-User-Authorization": f"Bearer {user_access_token}"},
)
```

An invalid forwarded token is rejected (`401`). It never falls back to the
application's own access.

### Admin UI sign-in

The admin UI picks how to authenticate at **build time** with
`NEXT_PUBLIC_AUTH_MODE`:

| Value | Use with | Behaviour |
|-------|----------|-----------|
| `none` (default) | `AUTH__MODE=disabled` | No credentials, as before. |
| `proxy` | `AUTH__MODE=trusted_header` | The UI and API sit behind the same authenticating proxy. Requests carry the proxy's session cookie. |
| `oidc` | `AUTH__MODE=jwt` | Authorization-code + PKCE sign-in against your IdP as a public client. The access token is kept in memory only. |

For `oidc`, register a public client whose redirect URI is
`https://<ui-host>/auth/callback`. Then pass the build args through a compose
override. `compose.yml` itself needs no change:

```yaml
# compose.override.yml
services:
  frontend:
    build:
      args:
        NEXT_PUBLIC_AUTH_MODE: oidc
        NEXT_PUBLIC_OIDC_ISSUER: https://sso.corp.internal/realms/acme
        NEXT_PUBLIC_OIDC_CLIENT_ID: rag-admin-ui
```

The IdP's access token must carry the audience in `AUTH__AUDIENCE`. The
sidebar shows the signed-in subject and roles. An expired session raises a
"sign in again" banner.

## 🧾 Audit logging

`AUDIT__LEVEL` turns on a structured audit trail in the platform's own
PostgreSQL (`audit_events`). It's a record a compliance team can query and
export: who asked what, which knowledge they were allowed to search, what was
retrieved, and what was answered.

| Level | Recorded |
|-------|----------|
| `off` (default) | Nothing. No database writes. |
| `metadata` | Everything below except text: the query and answer are kept only as SHA-256 digests. To check whether a particular question was asked, hash it and compare the digests. |
| `full` | As `metadata`, plus the query and answer text. |

**What produces an event**

| Action | When | Notable fields |
|--------|------|----------------|
| `query.answer` | `/api/v1/ask`, `/stream`, `/ask-agentic`, the playground, `/v1/chat/completions` (streaming too) | caller (end user and/or API key), application, knowledge bases *requested / permitted / dropped* by grants, retrieved chunk IDs with scores, model, cache hit, latency, outcome |
| `query.search` | `/api/v1/hybrid-search` | as above, without an answer |
| `admin.mutation` | every POST/PUT/PATCH/DELETE on the control plane | route template, path parameters, status code, caller |
| `access.denied` | any other `401`/`403` | route, caller if known |
| `audit.export` | an export of the audit log | format and filters |
| `audit.purge` | `make audit-purge` | cutoff, rows deleted, OS user |
| `uploads.cleanup` | `make uploads-cleanup` | days, files removed, bytes freed, OS user |
| `chat.attachment` | a document attached to or removed from a Conversation | event, Conversation and Attachment ids, type, size, tokens, content digest (never name or text) |
| `chat.purge` | the daily chat retention purge, `make chat-purge` | cutoff, days, Conversations deleted (counts only) |

Every event also has the `X-Request-ID`, the client IP (taken from
`X-Forwarded-For` only when the peer is in `AUTH__TRUSTED_PROXY_CIDRS`) and the
user agent. The outcome is `ok`, `denied` (the caller could read none of the
knowledge they asked about, or got a 401/403) or `error`. In the knowledge-base
fields, `null` means unrestricted or global and `[]` means none.

**Reading it.** Anyone with the `auditor` role can use the **Audit Log** page in
the admin UI or the API. Admins hold that role unless
`AUTH__ADMIN_CAN_READ_AUDIT=false`.

- `GET /api/v1/audit/events` accepts filters `actor`, `action`, `outcome`,
  `application_id`, `knowledge_base_id`, `request_id`, `since` and `until`.
  Results are newest first; pass `next_cursor` back as `cursor` for the next page.
- `GET /api/v1/audit/events/{id}` returns one event.
- `GET /api/v1/audit/export?format=csv|ndjson` streams every matching event.
  In CSV, cells a spreadsheet would execute are prefixed with `'`.

There is no API to change or delete events.

**When the audit write fails.** With `AUDIT__FAIL_MODE=log` (the default) the
error is logged and the response is served. With `deny`, an answer or export
that can't be audited is refused with `503`. A stream has already delivered its
answer by then, so it ends with an error event instead. Admin changes are
recorded after they complete, so a failed write never alters their response.

**Retention.** Nothing is deleted automatically.
`make audit-purge DAYS=365` (or `AUDIT__RETENTION_DAYS` plus `make audit-purge`)
deletes older events inside the `rag-api` container. It leaves an `audit.purge`
event in the same transaction. There is no HTTP endpoint for purging.

**Append-only at the database level.** For stricter deployments, have the API
connect as a role that can insert and read audit rows but not change them, and
run purges as a separate role. A table's owner can always grant its rights
back, so `audit_events` must be owned by a third role. Start the API once as
that owner so it creates the tables, then switch the API to `rag_app`:

```sql
ALTER TABLE audit_events OWNER TO rag_owner;
GRANT SELECT, INSERT ON audit_events TO rag_app;
GRANT SELECT, INSERT, DELETE ON audit_events TO rag_retention;  -- purge job only
```

**Limits.** Reads of the audit log itself are not recorded; exports are. For
`admin.mutation` events on create endpoints, `resource_id` is empty because the
new ID is only in the response body. A tamper-evidence hash chain is a planned
follow-up.

## 📈 Telemetry

Metrics and traces for operators: request rate, errors and latency per route and
per Application, dependency health (PostgreSQL, OpenSearch, Redis, the Inference
endpoint), tokens per Application and model, ingestion and Connector runs, and
one trace per request with a step for each stage. It is separate from the
[Audit log](#-audit-logging) (which says *who* did *what*): telemetry never holds
a question, an answer, Document text, or who asked. Everything stays on your
network, and everything is off by default.

**Turn it on.**

1. In `.env`, set a Grafana admin password (there is no default, and Grafana
   refuses to start without one) and enable export:

   ```bash
   GRAFANA_ADMIN_PASSWORD=<a long, unique password>
   OTEL__ENABLED=true
   ```

2. Start the stack, then restart the API so it picks up `OTEL__ENABLED`:

   ```bash
   make telemetry-start          # OpenTelemetry Collector, Prometheus, Tempo, Grafana
   docker compose up -d api      # (and `connector` if you use Connectors)
   ```

   `make telemetry-stop` removes the four containers and keeps their data.
   Plain `make start` never starts them. On a hardened stack use
   `make telemetry-start-hardened` instead (only Grafana is published).

3. Open Grafana at <http://localhost:3002> (`GRAFANA_PORT`), user `admin`. The
   Prometheus and Tempo data sources, three dashboards and four alert rules are
   already there, in the **RAG platform** folder. Prometheus itself is at
   <http://localhost:9090> (`PROMETHEUS_PORT`).

**The dashboards.**

| Dashboard | Shows |
|-----------|-------|
| **RAG platform: health** | Request rate, error rate and p95 latency (overall and per route); how many dependencies are failing, each dependency's success ratio and calls by outcome. |
| **RAG platform: capacity** | Prompt and completion tokens per minute by Application and model; time to first token; dependency latency; ingestion stage duration, queue wait and outcomes; Connector runs, duration and Documents changed. |
| **RAG platform: usage** | Answer requests per Application (failed ones included); out-of-scope refusals per Application; searches, chunks returned, empty results and skipped reranks per Knowledge Base (shown by ID); the answer-cache hit rate. |

**Alert rules.** *High API error rate* (over 5% server errors for 5 minutes),
*High API p95 latency* (over 60 seconds for 10 minutes; a streamed answer counts
until its last byte, so set it above your slowest normal answer), *A dependency
is failing* (over half its calls failing for 5 minutes) and *Ingestion is
failing* (more than two Documents failed in 30 minutes). A rule with no data
(nothing happened) is normal, not firing.

**Set the alert contact point.** None is shipped and none is assumed: until you
add one, a firing rule shows in Grafana under **Alerting > Alert rules** and
tells nobody. Add yours (email, Slack, a webhook) under **Alerting > Contact
points**, then route to it under **Alerting > Notification policies**. Email
also needs an SMTP server in Grafana's settings (`GF_SMTP_*` on the `grafana`
service). The rules themselves are files in `telemetry/grafana/provisioning/alerting/`.

**Settings.** The API and the `connector` read `OTEL__ENABLED`, `OTEL__ENDPOINT`,
`OTEL__SERVICE_NAME`, `OTEL__TRACE_SAMPLE_RATIO` (default `1.0`; metrics are
never sampled), `OTEL__EXPORT_TIMEOUT` and `OTEL__QUEUE_SIZE`, all documented in
`.env.example` (containers send to the bundled collector; `OTEL_COLLECTOR_ENDPOINT` points them at another). Retention is set on the stores: `PROMETHEUS_RETENTION` (default
`15d` for metrics) and `TEMPO_RETENTION` (default `72h`, three days, for
traces). The names, units and labels of every metric are in
[`docs/telemetry-metrics.md`](docs/telemetry-metrics.md); the deployment note
(localhost binding, the Grafana password, fail-open) is in
[`docs/deployment/telemetry.md`](docs/deployment/telemetry.md).

**From an Audit log event to its trace.** Every response carries an
`X-Request-ID` header, and the Audit log event of that request records the same
`request_id`, plus the `trace_id` in its `details` (when telemetry is on).

1. In the Audit Log page, or `GET /api/v1/audit/events?request_id=...`, open the
   event and copy `details.trace_id`.
2. In Grafana, open **Explore**, choose the **Tempo** data source, and either
   paste the trace ID (query type *TraceID*) or search by request ID with the
   TraceQL query `{ span.request_id = "<request id>" }`.

JSON log lines carry the same request ID and trace ID, so a request can also be
followed through the logs. If the request ID is all you have (from a user's
bug report, say), the TraceQL search finds the trace.

**Health.** The **System** page lists *Telemetry* as `disabled`, `exporting` or
`failing` (the collector is unreachable or refusing data). `failing` does not
make the platform report unhealthy: telemetry fails open.

## 💬 Employee chat

Employees sign in with the company identity and chat with the Applications they
may use (those whose every Knowledge Base they hold a Grant on). Answers stream,
with citations that open the source Document and passage.

- **Conversations** are stored per employee (new tables `chat_conversations`,
  `chat_messages`), keyed by the IdP subject. They can be
  listed, resumed, renamed and deleted by their owner. Nobody else can read one,
  admins included: another user's Conversation is a `404`. Follow-up questions are
  given the latest `CHAT__HISTORY_MESSAGES` messages as context, and retrieval also
  searches with the question they follow (an agentic Application receives that combined
  question but not the earlier answers).
- **Admins see counts, never text.** `GET /api/v1/chat-admin/conversation-counts`
  carries identifiers and counts only.
- **Retention.** Conversations are purged `CHAT__RETENTION_DAYS` (default 90; 0 keeps
  forever) after their last message, together with their messages. The
  API runs the purge daily; `make chat-purge` runs it on demand. A purge that deletes
  something leaves a `chat.purge` audit event with counts only.
- **Document Attachments.** An employee can attach PDF, DOCX, PPTX, XLSX, TXT, MD or CSV
  files (📎 in the chat) and ask about them. The type is decided from the file's
  **content** (PDF header, the parts inside an Office file, valid UTF-8 text) and must
  agree with its extension, so a renamed file is refused (`415`). Defaults, all
  configurable (`CHAT__ATTACHMENTS_PER_MESSAGE`, `CHAT__ATTACHMENT_MAX_MB`,
  `CHAT__CONVERSATION_ATTACHMENTS_MAX_MB`): 5 per message, 20 MB per document, 100 MB per
  Conversation; beyond them the error says which limit. Files are parsed with the platform's
  Docling parser and **only the extracted text is kept**; the upload is discarded.
  Attachments go into the prompt **in full** while together they fit
  `CHAT__ATTACHMENT_TOKEN_BUDGET` (default 8000 tokens, roughly 4 characters each). A
  document that would not fit is **chunked and embedded into a private partition of the chunk
  index** instead (shown as "searched" in the chat), and each question gets its best
  `CHAT__ATTACHMENT_PASSAGES` passages (default 6); its extracted text is then kept only as
  those chunks. The partition is filtered by owner **and** Conversation, and every
  Knowledge Base search excludes it, so these chunks never reach shared knowledge or another
  user or Conversation. Citations for such passages name the Attachment, not a Document.
  Removing an Attachment, deleting its Conversation, or the retention purge removes the
  chunks first (if the index is unreachable the delete answers `503` rather than leave them).
  The prompt tells the model the text is **untrusted data, not instructions**. Attachments
  are reachable only by the Conversation's owner, inside that Conversation (another user,
  admins included, gets `404`), can be removed one by one, and are deleted with the Conversation
  (so also by retention). Uploads and removals leave a `chat.attachment` audit event with
  metadata only (type, size, token count, a SHA-256 of the content), never the name or text.
  Assistants on the agentic strategy do not take attachments (`409`).
- **Image Attachments.** Off by default. An operator with a **multimodal chat model** sets
  `CHAT__IMAGES_ENABLED=true`; the chat then shows a 🖼 control and employees can attach PNG,
  JPG or WEBP images (`CHAT__IMAGE_MAX_MB`, default 10 MB each) and ask about them. When it
  is off the control is hidden (`GET /api/v1/chat/config` tells the UI) and the API refuses
  images with `415` ("Image upload is not enabled on this deployment"). Types are verified by
  content (a renamed file, or garbage behind an image header, is refused); images share the
  per-message and per-Conversation limits above and are kept (as bytes) so follow-up questions
  still see them. They are sent to the Application's chat model as **OpenAI-style
  `image_url` content parts** on the final user message; there is no separate vision
  pipeline and no capability detection, so a text-only model will simply fail or ignore them.
  Only OpenAI-compatible endpoints (`LLM__PROVIDER=openai-compatible`, e.g. vLLM, llama.cpp,
  Ollama's `/v1`) can take images; on the native Ollama client the send is refused with
  `409`. If the switch is turned off later, images already stored are no longer sent. Images follow the Conversation: owner-only, removed one by one, deleted with the
  Conversation and by retention. Audit records carry type, size and a digest only.
- **Per-user rate limits.** Each employee may send `CHAT__RATE_LIMIT_PER_MINUTE` messages
  a minute (default 30) and have `CHAT__MAX_CONCURRENT_GENERATIONS` answers being written
  at once (default 3); 0 turns a limit off. Beyond either, `POST .../messages` answers `429`
  with a `Retry-After` header and a friendly message the chat shows. Limits are per
  user, and a slot is freed when a stream finishes, fails or the browser disconnects (and a
  lost one expires after `CHAT__GENERATION_SLOT_SECONDS`). Counts live in Redis; **if Redis
  is unreachable the limits do not switch off**: each API worker counts in its own memory
  (so across several workers the effective limit is looser) and tries Redis again after 15 seconds.
- **Privacy defaults.** Chat audit records hold metadata and SHA-256 digests, never
  the question or answer, even at `AUDIT__LEVEL=full`.

**Upgrading a database that predates the removal of ratings.** Tables are created
with `create_all` and there are no migrations, so an existing database keeps an
orphaned, unused `chat_message_feedback` table. It is harmless; to reclaim it, run
`DROP TABLE chat_message_feedback;` manually.

The role-to-surface split is enforced on the server: `querier` reaches chat only,
`admin` the control plane, `auditor` the audit log; a user with several roles gets each.

## 🔄 Connectors

A **Connector** mirrors one source folder into one Knowledge Base, so a
department's file share stays searchable without anyone uploading files by hand.
It adds new files, updates changed ones and removes deleted ones. It reads
`.pdf`, `.txt` and `.md` files (other types are counted as skipped). The source
is either a **mounted folder** or an **S3-compatible bucket** (AWS S3, MinIO, …);
both behave identically.

1. **Mount the share** where the job runs (SMB/NFS mounted by the operating
   system, or a Docker bind mount) and set `CONNECTOR__MOUNT_ROOT` to the
   directory your shares live under. Mounted paths outside it are refused, and
   symbolic links are never followed, so the job cannot be aimed at the rest of
   the server. Mount shares read-only.
2. **Define it** (admin): `POST /api/v1/connectors` with
   `{"name": "hr-share", "knowledge_base_id": "…", "path": "/mnt/sources/hr"}`.
   `GET /api/v1/connectors` shows each one's last run; `PATCH` pauses it or
   repoints it; `DELETE` stops mirroring and leaves its Documents in place.
   **An S3-compatible bucket** instead takes
   `{"name": "bucket", "knowledge_base_id": "…", "source_type": "s3",
   "endpoint_url": "https://minio.internal:9000", "bucket": "docs",
   "prefix": "hr/", "access_key_ref": "env:MINIO_ACCESS_KEY",
   "secret_key_ref": "env:MINIO_SECRET_KEY"}`. Credentials are **only**
   `env:NAME` references, read from the job's environment when it runs (put the
   values in `.env`); a literal key is rejected, so nothing secret is stored in
   the database. The job contacts only the configured endpoint (path-style
   addressing, explicit credentials, no metadata-service lookups);
   `CONNECTOR__ALLOWED_S3_HOSTS` can restrict which hosts an admin may name.
3. **Run it** as a separate command, not inside the API process:
   `make connector-sync NAME=hr-share` (or `ALL=1`;
   `python -m src.cli.connector sync hr-share` inside the container).
   `DRY_RUN=1` reports what would change and changes nothing.

**On a schedule (optional).** The `connectors` compose profile runs every
enabled Connector on an interval in its own container, and is **off by
default** (plain `docker compose up` never starts it):

```bash
# .env: CONNECTOR_INTERVAL_MINUTES=60, CONNECTOR_MOUNT_DIR=/srv/shares (mounted read-only at /mnt/sources)
docker compose --profile connectors up -d connector
```

A failed round is logged and retried on the next interval; the schedule never
stops on its own. For cron or a systemd timer, call `make connector-sync ALL=1`
instead.

**Verified against MinIO.** `backend/tests/integration/test_connector_s3.py`
runs add, change, delete and unchanged-skip through the real boto3 client against
a live MinIO and OpenSearch (skipped when they are not running):

```bash
docker run -d -p 9000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data
docker run -d -p 9200:9200 -e discovery.type=single-node -e DISABLE_SECURITY_PLUGIN=true opensearchproject/opensearch:2.19.0
cd backend && uv run python -m pytest tests/integration/test_connector_s3.py
```

Each run prints a summary and exits non-zero if something went wrong:

```
hr-share: added 3, updated 1, removed 0, failed 1 (120 unchanged, 4 skipped as an unsupported type)
  FAILED policies/scan.pdf: No extractable text found in document
```

- **Only changes are processed.** A file whose size and modified time are
  unchanged is not even read; one that was touched but not changed (same
  content hash) is not re-ingested.
- **Updates are in place.** A changed file keeps its Document, so
  [restrictions](#document-restrictions) an admin set on it survive. New
  Documents are **unrestricted** until an admin restricts them.
- **Removed files really go.** Their Documents and indexed chunks are deleted, so
  withdrawn content stops being answerable. If the index is unreachable the
  Document stays and the removal is retried next run.
- **An empty source is not trusted.** If a share lists no files but files were
  synced before (an unmounted share looks exactly like that), the run refuses
  and removes nothing; `--allow-empty` (`ALLOW_EMPTY=1`) confirms it.
- **Failures don't stop the run.** A file that fails is reported (exit status 1)
  and retried on the next run. Exit status 2 means the run could not be made
  (unknown or disabled Connector, source unreachable, another run in progress,
  or ingestion refused because the index needs `make reindex`).
- **Audited.** Every run leaves a `connector.sync` audit event: counts and the
  names of added, updated, removed and failed files (never file content).

## 🧪 Sample-data check

`make ingest-samples` ingests the real sample folder (`data/File Examples`, git-ignored) into a scratch
Knowledge Base called *Sample data (scratch)* and prints, per file, what happened: added, replaced,
unchanged, skipped or failed, the chunk count, warnings (for example images that were not described) and
the error. A broken file (the 71-byte PPTX stub) shows as failed with its reason and never stops the run.
It is a local, opt-in check for judging ingestion quality on real documents and **is not part of CI**,
which uses small synthetic files. It runs in the `rag-api` container, so the stack must be up.

```bash
make ingest-samples                 # add new files, replace changed ones, skip unchanged ones, retry failed ones
make ingest-samples URLS=1          # also add the web links in Links.docx whose hosts are in INGESTION__URL_ALLOWED_HOSTS
make ingest-samples ASK=1           # then ask the questions in evals/sample-data-questions.yaml and print the Citations
make ingest-samples CLEAN=1         # remove the scratch Knowledge Base, its Documents, chunks and files
make ingest-samples SAMPLES=/path/to/other/folder
```

The questions file is a Golden set, so once you agree with the Citations it prints you can add
`expected` content and run it with `make eval` against an Application attached to the scratch
Knowledge Base ([`docs/evaluation.md`](docs/evaluation.md)). The exit status is 0 when the run
finished, whatever failed in it; 2 means it could not be made (no such folder, index needs `make reindex`).

## 📏 Evaluating answers

`make eval GOLDEN=evals/example-golden-set.yaml APP=<slug>` scores an Application against a
versioned Golden set: recall@k and MRR, an optional groundedness score, and permission checks
that run as a named caller and fail the command if restricted content comes back. Two reports can
be compared (`make eval-compare`), for example before and after enabling the reranker. See
[`docs/evaluation.md`](docs/evaluation.md).

## 🛡️ Production hardening

`compose.yml` publishes every service on every host interface, which is
convenient on a laptop and wrong anywhere else. The opt-in
`compose.hardened.yml` overlay removes that:

```bash
make start-hardened   # docker compose -f compose.yml -f compose.hardened.yml up --build -d
make health-hardened
```

| Service | Default stack | Hardened |
|---------|---------------|----------|
| API, admin UI | `0.0.0.0` | `127.0.0.1` only. Put your reverse proxy or ingress in front. |
| PostgreSQL, Redis, OpenSearch (+ Dashboards), Ollama | `0.0.0.0` | Not published. Reachable only by the other containers on `rag-network`. |
| Telemetry profile: Collector, Prometheus, Tempo | `127.0.0.1` | Not published. |
| Telemetry profile: Grafana | `127.0.0.1` | `RAG_BIND_ADDRESS` (loopback by default), still with a required admin password and no anonymous access. |

The containers talk to each other exactly as before (`postgres:5432`,
`opensearch:9200`, …), so nothing else changes. Optional variables:
`RAG_BIND_ADDRESS` publishes on a specific internal interface instead of
loopback, and `RAG_API_PORT` and `RAG_FRONTEND_PORT`
change the host ports. The admin UI calls the API from the browser at
`NEXT_PUBLIC_API_URL`, which is baked in at build time. Set it to the URL your
proxy serves the API on. It stacks with other overlays; put it last:
`docker compose -f compose.yml -f compose.override.yml -f compose.hardened.yml up -d`.

Plain `make health` reports OpenSearch and Ollama as "not responding" on a
hardened stack. That's the point: their ports are closed. `make
health-hardened` checks them from inside the network instead.

**The overlay is network isolation, and only that.** Before going live:

- Turn on identity: `AUTH__MODE=jwt` or `trusted_header` (see
  [Authentication](#-authentication)). For the admin UI, also set
  `NEXT_PUBLIC_AUTH_MODE`. If you use `trusted_header`, make sure the API is
  reachable only through the proxy that sets the headers.
- Turn on the audit log: `AUDIT__LEVEL=metadata` or `full`.
- Change the default credentials. PostgreSQL's `rag_password` is in
  `compose.yml` and `POSTGRES_DATABASE_URL`.
- Know what is still unauthenticated *inside* `rag-network`: OpenSearch runs
  with its security plugin disabled, and Redis has no password by default.
  Anything that can join the network can read the index and the answer cache.
  Enabling OpenSearch TLS and credentials is a planned follow-up.

Roll back by starting without the overlay (`make start`).

## 🧪 Testing

The virtualenv lives at the repo root (`.venv`), not in `backend/`. The Make
targets handle this; when calling `uv` directly, point it there first:

```bash
cd backend
export UV_PROJECT_ENVIRONMENT=../.venv  # otherwise uv creates backend/.venv
uv sync                                 # install dependencies
uv run python -m pytest                 # run the backend test suite
uv run python -m pytest --ignore=tests/integration   # offline only
uv run python -m pytest --cov=src       # with coverage

cd frontend && npm run build            # frontend production build + type check
```

Or from the repo root: `make test`, `make frontend-build`.

## 📁 Project Structure

```
.
├── compose.yml              # Full local stack (API, frontend, datastores)
├── compose.hardened.yml     # Opt-in overlay: publish only the API/UIs, on 127.0.0.1
├── Makefile                 # Common developer commands
├── .env / .env.example      # Shared config for compose and the backend
├── backend/                 # FastAPI backend
│   ├── Dockerfile           # Backend image
│   ├── pyproject.toml       # Backend dependencies (managed by uv)
│   ├── uv.lock
│   ├── .env.test            # Test-only env, loaded by pytest-env
│   ├── src/
│   │   ├── main.py          # App entrypoint & router wiring
│   │   ├── config.py        # Settings
│   │   ├── routers/         # HTTP endpoints
│   │   ├── services/        # Ingestion, indexing, retrieval, agents, providers
│   │   ├── repositories/    # Data access
│   │   ├── models/          # SQLAlchemy models
│   │   └── schemas/         # Pydantic schemas
│   └── tests/               # Backend test suite
└── frontend/                # Next.js 14 Admin Control Plane
    ├── Dockerfile
    ├── package.json
    └── src/app/             # App Router pages
```

## 📄 License

MIT — see [LICENSE](LICENSE).
