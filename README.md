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
make uploads-cleanup DAYS=14  # remove stale uploaded files (off unless run; DRY_RUN=1 to count)
make reindex             # rebuild the chunk index on lucene / a new embedding model, then swap the alias (DRY_RUN=1 to preview)
make connector-sync NAME=hr-share  # mirror a Connector's source folder into its Knowledge Base (ALL=1, DRY_RUN=1)
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
| Employee chat (querier role) | `/api/v1/chat` (applications, conversations, messages, feedback, documents) |
| Chat counts, ratings, shared exchanges (admin) | `/api/v1/chat-admin` |
| OpenAI-compatible chat | `POST /v1/chat/completions` |

Full, interactive documentation is available at `/docs`.

## 🖥️ Admin Control Plane (Frontend)

A Next.js 14 admin UI (`frontend/`) for managing knowledge bases, document
ingestion, RAG applications (with a retrieval playground), model providers,
retrieval profiles, and system health, plus an audit log viewer for auditors.

It is also the employees' chat app. Each role reaches its own surface, enforced
on the server and mirrored in the UI: `querier` gets **Chat** only, `admin` the
control plane, `auditor` the audit log (a user with several roles gets each).
In Chat an employee sees only the Applications whose *every* Knowledge Base they
hold a Grant on, gets the answer streamed, and can open each citation to see the
source Document with the cited passage marked. Conversations are not stored yet.

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
- **Uploads** (`INGESTION__*`) — document upload size cap (`MAX_UPLOAD_MB`, default
  50; larger uploads get `413`), the upload directory, and an opt-in retention for
  stale files (`make uploads-cleanup`). Stored files are named by content hash;
  nothing from the uploaded filename reaches the file system.
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
| `querier` | The employee chat (`/api/v1/chat`: the Applications they may use, streamed answers with citations, source Documents), `/api/v1/ask`, `/stream`, `/ask-agentic`, `/feedback`, `/hybrid-search`, and `/v1/chat/completions` |
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

## 💬 Employee chat

Employees sign in with the company identity and chat with the Applications they
may use (those whose every Knowledge Base they hold a Grant on). Answers stream,
with citations that open the source Document and passage.

- **Conversations** are stored per employee (new tables `chat_conversations`,
  `chat_messages`, `chat_message_feedback`), keyed by the IdP subject. They can be
  listed, resumed, renamed and deleted by their owner. Nobody else can read one,
  admins included: another user's Conversation is a `404`. Follow-up questions are
  given the latest `CHAT__HISTORY_MESSAGES` messages as context, and retrieval also
  searches with the question they follow (an agentic Application receives that combined
  question but not the earlier answers).
- **Admins see counts and ratings, never text.** `GET /api/v1/chat-admin/conversation-counts`,
  `/ratings`, and `/ratings/summary` carry identifiers and the user's own comment.
  The one exception is an exchange an employee explicitly **shares with admins**
  when rating an answer: only then does `/shared-exchanges` show that one question
  and answer, and `/shared-exchanges/export?application_id=` writes them as Golden
  set candidates for the [eval harness](docs/evaluation.md). Withdrawing the share
  or deleting the Conversation takes the exchange back.
- **Retention.** Conversations are purged `CHAT__RETENTION_DAYS` (default 90; 0 keeps
  forever) after their last message, together with their messages and ratings. The
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
  the question or answer, even at `AUDIT__LEVEL=full`. Chat questions and answers stay
  out of Langfuse traces (which carry metadata only) unless `CHAT__TRACE_CONTENT=true`.

The role-to-surface split is enforced on the server: `querier` reaches chat only,
`admin` the control plane, `auditor` the audit log; a user with several roles gets each.

## 🔄 Connectors

A **Connector** mirrors one source folder into one Knowledge Base, so a
department's file share stays searchable without anyone uploading files by hand.
It adds new files, updates changed ones and removes deleted ones. It reads
`.pdf`, `.txt` and `.md` files (other types are counted as skipped).

1. **Mount the share** where the job runs (SMB/NFS mounted by the operating
   system, or a Docker bind mount) and set `CONNECTOR__MOUNT_ROOT` to the
   directory your shares live under. Mounted paths outside it are refused, and
   symbolic links are never followed, so the job cannot be aimed at the rest of
   the server. Mount shares read-only.
2. **Define it** (admin): `POST /api/v1/connectors` with
   `{"name": "hr-share", "knowledge_base_id": "…", "path": "/mnt/sources/hr"}`.
   `GET /api/v1/connectors` shows each one's last run; `PATCH` pauses it or
   repoints it; `DELETE` stops mirroring and leaves its Documents in place.
3. **Run it** as a separate command, not inside the API process:
   `make connector-sync NAME=hr-share` (or `ALL=1`;
   `python -m src.cli.connector sync hr-share` inside the container).
   `DRY_RUN=1` reports what would change and changes nothing.

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
