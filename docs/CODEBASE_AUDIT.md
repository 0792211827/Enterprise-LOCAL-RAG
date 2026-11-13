# Enterprise-LOCAL-RAG: Codebase Audit

Audited at branch `feature/owui-gap-analysis`, commit `4ce0a6b` (41 commits, clean tree). All paths are repository-relative. Companion to [`FEATURE_REGISTER.md`](FEATURE_REGISTER.md) and [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md).

## 1. Architecture overview

A local-first RAG platform. **Backend:** FastAPI, Python 3.12, `uv` (`backend/`). **Admin UI:** Next.js 14 App Router, TanStack Query (`frontend/`). **Data/services (Docker Compose):** PostgreSQL, OpenSearch 2.19 (single shared hybrid index), Ollama 0.11.2, Redis, Langfuse v3 (with ClickHouse, MinIO, its own Postgres/Redis). No external AI APIs are required.

```
Browser (admin UI :3001) ─┐                    ┌─ PostgreSQL  (KBs, docs, chunks, apps, providers,
Host apps / OpenAI SDKs ──┤                    │               api keys, grants, audit_events)
                          ▼                    │
                     FastAPI (:8000) ──────────┼─ OpenSearch  (BM25 + kNN + RRF; chunks tagged knowledge_base_id)
   middleware: RequestContext → Audit → CORS   ├─ Ollama      (LLM, and embeddings when EMBEDDING__PROVIDER=ollama)
   auth: per-router role deps (+ handler auth) ├─ Redis       (answer cache, scope-keyed)
                                               └─ Langfuse    (optional tracing)
```

### Layers

`routers/` (HTTP) → `services/` (logic) → `repositories/` (SQLAlchemy over `models/`); Pydantic schemas in `schemas/`. Tables are created by `Base.metadata.create_all` (`db/interfaces/postgresql.py`); there are no migrations, so existing tables never alter (see ADR-0003). New models must be imported in `src/models/__init__.py`.

### Service lifecycle / DI

`src/main.py` `lifespan` builds each collaborator through a `make_*` factory onto `app.state` (DB, OpenSearch, PDF parser, embeddings, Ollama, LLM provider, optional VLM, Langfuse, Redis cache, audit recorder, authenticator) and creates the OpenSearch index if missing. `src/dependencies.py` exposes them as `Annotated` deps (`OpenSearchDep`, `SessionDep`, `LLMProviderDep`, `OllamaDep`, `AuditDep`, `PrincipalDep`, …). Per-request services (`IngestionService`, agentic RAG) are assembled there. `tests/api/conftest.py` patches every `make_*` factory so API tests are offline.

### Configuration

`src/config.py`: nested pydantic-settings groups with `__` prefixes: `EMBEDDING__`, `LLM__`, `VLM__`, `PDF_PARSER__`, `CHUNKING__`, `OPENSEARCH__`, `LANGFUSE__`, `REDIS__`, `AUTH__`, `API_KEYS__`, `AUDIT__`. Reads `.env` from repo root and `backend/`. Documented in `.env.example` and the README. **Defaults are open:** `AUTH__MODE=disabled`, `API_KEYS__ENFORCE=false`, `AUDIT__LEVEL=off`.

## 2. Important modules

| Area | Path | Role |
|---|---|---|
| Identity | `backend/src/auth/{principal,authenticators,dependencies,completions}.py` | `Principal`; `Disabled`/`Jwt`/`TrustedHeader` authenticators; `require_role`; `/v1` key-or-user authorisation |
| Policy | `backend/src/main.py` (`QUERIER`/`ADMIN`/`AUDITOR`) | The whole route policy, applied once per router |
| Retrieval scope | `backend/src/services/access/scope.py`, `repositories/grants.py`, `models/access.py` | One resolver: which KB IDs a caller may read |
| Retrieval | `services/opensearch/{client,query_builder,index_config_hybrid}.py` | Hybrid query + RRF pipeline; KB filter; empty-scope-matches-nothing |
| Answer paths | `routers/{ask,agentic_ask,hybrid_search,chat_completions,applications}.py`, `services/applications/answer.py` | Legacy/global vs application path |
| Agent | `services/agents/agentic_rag.py`, `agents/nodes/*`, `agents/context.py` | LangGraph `StateGraph` |
| Ingestion | `routers/documents.py`, `services/ingestion/service.py`, `services/pdf_parser/*`, `services/indexing/text_chunker.py` | Upload → parse → chunk → embed → index |
| Providers | `services/providers/{llm,embeddings,vlm}/*`, `services/ollama/client.py` | Interface + implementations + factory per kind |
| Audit | `models/audit.py`, `services/audit/*`, `middleware/audit.py`, `routers/audit.py`, `cli/audit_purge.py` | Append-only trail, export, purge |
| Observability | `services/langfuse/*` | Optional tracing with caller attribution |
| Cache | `services/cache/client.py` | Redis answer cache; key includes scope digest |
| Deploy | `compose.yml`, `compose.hardened.yml`, `compose.localports.yml`, `Makefile` | See §6 |
| Frontend | `frontend/src/app/*`, `frontend/src/lib/api/{client,resources,types}.ts` | Components never call `fetch` directly |

## 3. API route inventory (53 method+path entries)

Enumerated programmatically from `app.routes`; the policy is pinned by `tests/unit/test_route_policy.py`. "Guard" is the dependency that decides access (`-` = none).

| Group | Method + path | Guard |
|---|---|---|
| Public | `GET /api/v1/health` | - (liveness) |
| Any authenticated | `GET /api/v1/auth/me` | `get_principal` (401 if unauthenticated) |
| OpenAI-compatible | `POST /v1/chat/completions` | in-handler: app API key and/or user token; key required when `API_KEYS__ENFORCE=true` |
| Query | `POST /api/v1/{ask, stream, ask-agentic, feedback}`, `POST /api/v1/hybrid-search/` | querier |
| Knowledge bases | `GET,POST /api/v1/knowledge-bases`; `GET /knowledge-bases/access-check`; `GET,PATCH,DELETE /knowledge-bases/{kb_id}`; `GET,PUT /knowledge-bases/{kb_id}/grants`; `GET,POST /knowledge-bases/{kb_id}/documents` | admin |
| Documents/jobs | `GET /documents`; `GET,DELETE /documents/{id}`; `POST /documents/{id}/reprocess`; `GET /ingestion-jobs`; `GET /documents/{id}/ingestion-job` | admin |
| Applications | `GET,POST /applications`; `GET,PATCH,DELETE /applications/{id}`; `POST /applications/{id}/ask` | admin |
| API keys | `GET,POST /applications/{id}/api-keys`; `POST …/{key_id}/rotate`; `DELETE …/{key_id}` | admin |
| Providers | `GET,POST /providers`; `GET,PATCH,DELETE /providers/{id}`; `POST …/test`; `GET …/models` | admin (note: server-side calls to admin-supplied URLs) |
| Retrieval configs | `GET,POST /retrieval-configurations`; `GET,DELETE /retrieval-configurations/{id}` | admin (no update route) |
| System | `GET /system/{health,stats,security,gpu}` | admin |
| Audit | `GET /audit/{status,events,events/{id},export}` | auditor (admin included unless `AUTH__ADMIN_CAN_READ_AUDIT=false`) |

**Verified live (JWT mode):** every non-public route above returned 401 with no token. Querier and auditor tokens received 403 on admin routes. The Live checks are in the register (§3.8).

## 4. Data flows

### 4.1 Authentication and authorisation

1. `RequestContextMiddleware` (outermost) assigns `X-Request-ID` and the client IP; it honours `X-Forwarded-For` only from `AUTH__TRUSTED_PROXY_CIDRS`.
2. `AuditMiddleware` records admin mutations and 401/403 outcomes.
3. Per-router `require_role(...)` resolves the `Principal` via the configured authenticator, then checks role. JWT mode validates signature (JWKS or static PEM), issuer, audience and expiry. Role and group values come from configurable claim paths. Trusted-header mode reads headers only from trusted proxy CIDRs.
4. `ADMIN ⊃ QUERIER`; `ADMIN ⊃ AUDITOR` unless disabled; `QUERIER`/`AUDITOR` are independent.
5. A token with **no** mapped role yields 401 (not 403) at authentication.

### 4.2 Retrieval scope (KB isolation)

Every search path calls `resolve_scope(principal, session, settings)` and intersects the request with it (`RetrievalScope.restrict`): `None` = unrestricted (auth disabled, admin bypass, or application API key), `[]` = nothing (OpenSearch is skipped / matches nothing). Grants are keyed on IdP claim values (`group`, `role`, `subject`, `everyone`). Cache keys include a scope digest. The application path additionally intersects with the application's attached KBs.

### 4.3 Ingestion

`POST /knowledge-bases/{kb}/documents` → SHA-256 dedupe per KB → `.txt`/`.md` decoded in place, other types written under `tempfile.gettempdir()/rag-uploads/<kb>/<checksum>-<filename>` → `Document` + `IngestionJob` rows (QUEUED) → FastAPI `BackgroundTasks` runs `IngestionService`: parse (Docling) → chunk → embed (provider) → bulk index (chunks tagged `knowledge_base_id`) → chunk rows in Postgres → status/stage updates. Reprocess clears chunks and re-queues. Live: two markdown docs reached `COMPLETED` via Ollama `bge-m3` embeddings.

### 4.4 Answering

- **Application path** (`services/applications/answer.py`): shared by `POST /applications/{id}/ask` and `POST /v1/chat/completions`. Reads `model_configuration.llm_model` only; always uses the global `LLMProvider` and the traditional pipeline.
- **Legacy path:** `POST /ask`, `/stream` (global search + the configured `LLMProvider`), `POST /ask-agentic` (LangGraph; its nodes get their chat model from the configured provider via `Context.chat_model_source`), `POST /hybrid-search/` (retrieval only).
- **Agentic graph:** `guardrail → (out_of_scope → END | retrieve → tool_retrieve → grade_documents → rewrite_query ↺ retrieve | generate_answer → END)`.
- **Hybrid query:** OpenSearch `hybrid` query (BM25 + kNN, `nmslib`/cosine, 1024-dim hardcoded) fused by an RRF search pipeline; KB filter is a `terms` filter applied on both legs. There is no reranker.

### 4.5 Audit

`audited_query`/`audited_stream` wrap query paths; the middleware covers admin changes and denials. Rows go to `audit_events` (Postgres). Levels: `off | metadata | full`. `fail_mode=log|deny`. Export streams NDJSON/CSV and is itself audited. Purge is the `make audit-purge` CLI.

## 5. Background jobs and process model

- Ingestion runs in-process via FastAPI `BackgroundTasks` (no queue, no worker, no retry/resume if the API restarts mid-job). Admin-only; not caller-scoped.
- No scheduler exists. Audit purge is manual by design.
- Single API process; no horizontal-replica design (deferred; see the register §5).

## 6. Deployment and runtime configuration

- `compose.yml`: all services with host ports; GPU **not** configured for any service.
- `compose.localports.yml`: machine-specific remap (API→8020, Postgres→5442, Langfuse→3010) and Ollama embeddings for `bge-m3`. Not for upstream.
- `compose.hardened.yml`: removes host ports from data stores; binds API/UI/Langfuse to `RAG_BIND_ADDRESS` (default `127.0.0.1`). OpenSearch/Redis still unauthenticated inside `rag-network`.
- Makefile targets: `setup`, `test`, `lint`, `format`, `start`, `stop`, `start-hardened`, `health`, `health-hardened`, `audit-purge`.
- Frontend `NEXT_PUBLIC_API_URL` is baked at build time (compose build arg).

## 7. Test inventory and commands

```bash
make setup                                   # uv sync into ./.venv  (repo-root venv)
make test                                    # full backend suite (includes integration)
cd backend && export UV_PROJECT_ENVIRONMENT=../.venv
uv run python -m pytest --ignore=tests/integration   # offline subset (what was run here)
uv run ruff check . ; uv run ruff format --check .
cd ../frontend && npm run build              # frontend type-check; there is no test suite
```

Test layout: `tests/unit/` (auth, providers, services incl. scope/audit/OpenSearch builders, agents, wiring regressions, route policy), `tests/api/` (offline lifespan via `asgi-lifespan`), `tests/integration/` (live services; not run). `tests/unit/test_wiring_regressions.py` pins collaborator signatures. Update it when a factory or client signature changes.

## 8. Baseline results (2026-11-11)

| Check | Result |
|---|---|
| `pytest --ignore=tests/integration` | **529 passed**, 0 failed, 23 warnings (an un-awaited `AsyncMock` in `test_nodes.py`) |
| `ruff check .` | pass |
| `ruff format --check .` | **45 files would be reformatted** (pre-existing; not changed) |
| `mypy` | not run; `ignore_errors = true` in config makes it near-vacuous |
| `npm run build` (frontend) | pass |
| `npm run lint` (frontend) | **BLOCKED**: no ESLint config, so `next lint` starts an interactive setup wizard |
| `tests/integration/` | **not run**: needs live services and a model |
| Live stack smoke | `api`+`postgres`+`opensearch`+`redis`+`ollama` up; `/health` healthy; 44 live checks in JWT mode (see the register). Real behavioural differences: no-role tokens get 401 not 403, and admin can call `/ask`. Both are by design |
| Frontend/Langfuse/Dashboards containers | **not started** (not needed for the API checks) |
| GPU, vLLM, TGI, real IdP/JWKS, OIDC browser flow, hardened overlay live | **BLOCKED**: no hardware, no endpoints, no IdP, port overlap |

The live checks changed state in the developer's existing stack volumes: two temporary KBs and their documents were created and deleted through the API, and metadata-level audit events remain (append-only). The containers were stopped afterwards to restore the prior state.

## 9. Risks

| # | Risk | Notes |
|---|---|---|
| R1 | **Open by default** | `AUTH__MODE=disabled` is the shipped default; a fresh `make start` exposes all admin routes. Deployment docs must say so. `GET /system/security` reports the actual state |
| R2 | Provider `/test` and `/models` call admin-supplied URLs (SSRF-class, admin-only) | No host allowlist exists |
| R3 | nmslib kNN post-filtering | Narrow-scope searches can under-return. No leak. Durable fix is the lucene engine + reindex (ADR-0004) |
| R4 | Hardcoded 1024 dimension | Changing the embedding model breaks indexing |
| R5 | OpenSearch and Redis unauthenticated inside the network | Documented; hardened overlay depends on network isolation |
| R6 | Upload handling | No size cap/type allowlist/cleanup; filename is embedded in the path |
| R7 | In-process ingestion | Job lost on restart; stuck `QUEUED`/`PROCESSING` rows are possible |
| R8 | No migrations | Column changes to existing tables need manual work; plans use new tables only (ADR-0003) |
| R9 | Audit rows are append-only only by convention | See register §3.11 |
| R10 | Only Ollama, Ollama `/v1` and llama.cpp are verified | vLLM and TGI are contract-tested only (no GPU host). Small models can misbehave in the agent (register §3.2) |

## 10. Provenance of earlier analysis (folded in)

The earlier `docs/audit/` set (Phases 1–4: current-architecture gaps, Open WebUI reference notes, hardware/backend reference notes, feature gap comparison and integration plan) has been folded into these documents. The designs for the remaining work are in [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) and the decisions in [`decisions/`](decisions/). The original files remain in git history (commits `75f770c` through `4ce0a6b`). The findings still relevant:

- **Phase 1 is obsolete for security.** It reported "no authentication anywhere, 42 routes, keys not enforced, legacy endpoints search all KBs". Every one of those is now fixed and tested (§3, §4.2). The 42-route figure refers to an earlier state; the current count is 53 method+path entries.
- **Open WebUI positioning.** OWUI is built to *be* the app users log into: its own users table, roles (`admin/user/pending`), its own HS256 JWT, group feature-permissions OR-merged, owner + access-grant table, a file-based audit log with chat paths excluded. Enterprise-LOCAL-RAG is built to sit *behind the buyer's identity and perimeter*: take the authorisation data model and query-time re-checking, do **not** build user/session/password machinery (ADR-0001, ADR-0002).
- **OWUI backends.** It has no vLLM/TGI/TensorRT-specific code; everything is an "OpenAI connection" with N base URLs, normalised to the OpenAI shape. GPU enablement is a compose overlay reserving GPUs on the Ollama container; it does no GPU detection for inference. It picks a torch device only for in-process embedding/reranker models. Our `LLMProvider` ABC is a stronger abstraction, but most code paths bypass it. (Register §3.13.)
- **Not planned**: H6 multi-connection catalogue and vector-store abstraction, H7 in-process llama-cpp/Triton adapters, F2 end-user chat UI, F3 plugin/tool system (reconsider only out-of-process, role-gated and audited, never in-process Python).

## 11. As-built notes carried over from the security-track plan

- **Audit status** is `GET /api/v1/audit/status` (auditor role), not a field on `/system/security`, which is admin-only and would hide it from a non-admin auditor.
- **Extra audit actions:** `access.denied` (recorded by the middleware), `audit.export` (recorded *before* data is sent, so `deny` mode can refuse an unauditable export) and `audit.purge` (written in the same transaction as the delete, even at level `off`). A `details` JSON column holds action-specific extras.
- **Cache hits** record `retrieved = NULL` with cited titles in `details`, rather than claiming chunks were retrieved. Admin-mutation audit failures are log-only because the change has already committed. The Langfuse request ID is attached on `/ask` and `/stream`, but not on the agentic service, which builds its own traces.
- **Hardened overlay:** published ports come from `RAG_BIND_ADDRESS`, `RAG_API_PORT`, `RAG_FRONTEND_PORT`, `RAG_LANGFUSE_PORT`. Removed ports use `!reset []`. `make health-hardened` checks OpenSearch and Ollama from inside the network through the API container. The README checklist covers default credentials, Redis having no password, and the Langfuse media-upload endpoint.
- **Accepted behaviours:** `knowledge_base_ids=[]` means "match nothing" (a mode-independent semantic change made in S3); `AUTH__UNGRANTED_KB_ACCESS` defaults to `deny`; `AUDIT__FAIL_MODE` defaults to `log`. New dependencies added by the track: `pyjwt[crypto]` (backend) and an OIDC client (frontend). `langchain-openai` is planned for roadmap Phase 4.
