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
  applications, providers, retrieval profiles, and system health.
- **Local-first providers** — embeddings (HuggingFace / Ollama /
  OpenAI-compatible), LLM (Ollama / OpenAI-compatible), optional VLM.
- **Observability** — self-hosted Langfuse tracing and Redis caching.
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
                          Langfuse observability (:3000)
```

## 🧩 Services & Ports

| Service | URL | Purpose |
|---------|-----|---------|
| **API** | http://localhost:8000 | FastAPI backend |
| **API docs** | http://localhost:8000/docs | Interactive OpenAPI UI |
| **Admin Control Plane** | http://localhost:3001 | Next.js admin frontend |
| **Langfuse** | http://localhost:3000 | RAG tracing & monitoring |
| **OpenSearch** | http://localhost:9200 | Hybrid search engine |
| **OpenSearch Dashboards** | http://localhost:5601 | Search engine UI |
| **Ollama** | http://localhost:11434 | Local LLM / embeddings runtime |
| **PostgreSQL** | localhost:5432 | Application metadata |
| **Redis** | localhost:6379 | Response cache |

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
| Agentic RAG | `/api/v1/ask-agentic`, `/api/v1/feedback` |
| Knowledge bases | `/api/v1/knowledge-bases` |
| Documents | `/api/v1/documents`, `/api/v1/ingestion-jobs` |
| Applications | `/api/v1/applications` |
| Application API keys | `/api/v1/applications/{id}/api-keys` |
| Providers | `/api/v1/providers` |
| Retrieval configs | `/api/v1/retrieval-configurations` |
| System | `/api/v1/system` |
| Caller identity | `GET /api/v1/auth/me` |
| Active access controls | `GET /api/v1/system/security` |
| Audit log (auditor role) | `/api/v1/audit` |
| OpenAI-compatible chat | `POST /v1/chat/completions` |

Full, interactive documentation is available at `/docs`.

## 🖥️ Admin Control Plane (Frontend)

A Next.js 14 admin UI (`frontend/`) for managing knowledge bases, document
ingestion, RAG applications (with a retrieval playground), model providers,
retrieval profiles, and system health, plus an audit log viewer for auditors.

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
```

The browser calls the API at `http://localhost:8000` (configurable via the
`NEXT_PUBLIC_API_URL` build arg in `compose.yml`). The backend enables CORS for
`localhost:3000/3001` out of the box.

## ⚙️ Configuration

All configuration is environment-driven; see `.env.example` for the full,
documented list. Key groups:

- **Embeddings** (`EMBEDDING__*`) — provider, model, dimension, device.
- **LLM** (`LLM__*`) — provider, endpoint, model, timeout.
- **VLM** (`VLM__*`) — optional vision-language model (off by default).
- **OpenSearch** (`OPENSEARCH__*`) — index and hybrid-search settings.
- **Chunking** (`CHUNKING__*`) — chunk size, overlap, section-based mode.
- **Redis** (`REDIS__*`) and **Langfuse** client (`LANGFUSE__*` — note the
  double underscore; single-underscore `LANGFUSE_*` names configure the
  self-hosted Langfuse server in compose, not the backend).
- **Authentication** (`AUTH__*`) — see below. Off by default.
- **Audit log** (`AUDIT__*`) — see [Audit logging](#-audit-logging). Off by default.

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
| `querier` | `/api/v1/ask`, `/stream`, `/ask-agentic`, `/feedback`, `/hybrid-search`, and `/v1/chat/completions` |
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
> `docs/audit/integration-plan.md`).

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
retrieved, and what was answered. It is separate from Langfuse, which remains
the optional debugging and observability layer.

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
| API, admin UI, Langfuse UI | `0.0.0.0` | `127.0.0.1` only. Put your reverse proxy or ingress in front. |
| PostgreSQL, Redis, OpenSearch (+ Dashboards), Ollama, ClickHouse, Langfuse's Postgres/Redis/MinIO/worker | `0.0.0.0` | Not published. Reachable only by the other containers on `rag-network`. |

The containers talk to each other exactly as before (`postgres:5432`,
`opensearch:9200`, …), so nothing else changes. Optional variables:
`RAG_BIND_ADDRESS` publishes on a specific internal interface instead of
loopback, and `RAG_API_PORT`, `RAG_FRONTEND_PORT` and `RAG_LANGFUSE_PORT`
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
  `compose.yml` and `POSTGRES_DATABASE_URL`. Regenerate the Langfuse secrets
  in `.env` (see `.env.example`).
- Know what is still unauthenticated *inside* `rag-network`: OpenSearch runs
  with its security plugin disabled, and Redis has no password by default.
  Anything that can join the network can read the index and the answer cache.
  Enabling OpenSearch TLS and credentials is a planned follow-up.
- The overlay also closes Langfuse's browser media-upload endpoint (MinIO on
  `:9090`). It's used only for multimodal trace attachments, which this
  platform never sends.

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
├── compose.yml              # Full local stack (API, frontend, datastores, Langfuse)
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
