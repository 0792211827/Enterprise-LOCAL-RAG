# Current architecture: identity, permission and audit gaps

*Phase 1 of the Open WebUI gap analysis. This is a read-only audit of branch
`feature/owui-gap-analysis` at `d2d90c7`. File references are relative to
`backend/` unless noted.*

## TL;DR

- **No authentication anywhere.** None of the 42 HTTP routes takes an identity,
  and there is no auth middleware. The only middleware is CORS. The
  per-application API keys are minted and hashed but **not enforced**:
  `/v1/chat/completions` serves any request, with or without a key.
- **No owner, tenant or ACL field on any table.** The only isolation boundary
  is `knowledge_base_id`, and it is applied only on the application answer path.
  The legacy `/ask`, `/stream`, `/hybrid-search` and `/ask-agentic` endpoints
  search **every knowledge base in the index**.
- **A good seam already exists for permission-aware retrieval.** Every search
  goes through `OpenSearchClient.search_unified` / `search_chunks_vector`,
  which already take `knowledge_base_ids` and apply them to *both* the BM25 and
  kNN legs of the hybrid search. Permission filtering can extend that seam
  rather than being threaded through each caller.
- **Langfuse is not an audit log.** Tracing is optional, asynchronous,
  best-effort (failures are swallowed), and hardcodes `user_id="api_user"`. It
  covers only the legacy and agentic paths. **The application path (playground
  and OpenAI-compatible endpoint) is not traced at all.** Retrieved chunk IDs
  are never recorded anywhere.
- **The frontend has no concept of a user.** Nothing handles login, sessions
  or tokens. Every admin action is available to anyone who can reach
  `:3001` or `:8000`.

---

## 1. Request path traces

Insertion points are labelled **[I#]** and collected in the table at the end
of this section.

### 1a. Legacy `POST /api/v1/ask` (`routers/ask.py:85`)

```
HTTP ─► CORSMiddleware (main.py:116)                      [I1] no auth middleware
     ─► ask_question(AskRequest, OpenSearchDep, EmbeddingsDep,
                     OllamaDep, LangfuseDep, CacheDep)     [I2] no identity dependency
     ─► RAGTracer.trace_request("api_user", query)         [I3] identity hardcoded
     ─► cache_client.find_cached_response(request)          [I4] cache key has no identity/scope
     ─► _prepare_chunks_and_sources()
          ├─ embeddings.embed_query(query)
          └─ opensearch.search_unified(query, embedding, size, categories,
                                       use_hybrid, min_score)   [I5] NO knowledge_base_ids:
                                                                     searches the whole index
     ─► RAGPromptBuilder.create_structured_prompt(query, chunks)
     ─► ollama_client.generate_rag_answer(query, chunks, model)  [I6] model is caller-chosen
     ─► rag_tracer.end_request(trace, answer)               [I7] no durable record
     ─► cache_client.store_response(request, response)      [I4]
     ─► AskResponse(query, answer, sources, chunks_used)
```

`POST /api/v1/stream` (`routers/ask.py:176`) follows the same path, streamed.
One detail matters later: the generator runs *after* the handler returns (the
chat-completions router documents the same trap at
`routers/chat_completions.py:120`). Any identity or audit context must
therefore be resolved to plain values before the `StreamingResponse` is
created.

### 1b. Application path: `POST /api/v1/applications/{id}/ask` and `POST /v1/chat/completions`

This is the path buyers will actually integrate against. Both callers share
`services/applications/answer.py`, which is the right design and is the single
place to add identity-aware retrieval and auditing.

```
HTTP ─► create_chat_completion (routers/chat_completions.py:82)
     │     ApplicationRepository.get_by_slug(payload.model)      [I8] app is looked up by a public slug
     │     _record_key_usage(session, authorization)             [I9] key parsed but NOT enforced,
     │                                                                 and NOT checked against this app
     └─or─ ask_application (routers/applications.py:150)          [I2] no identity
     ─► answer_for_application() / retrieve_for_application()  (answer.py:42)
          kb_ids = [kb.id for kb in application.knowledge_bases]  [I10] the ONLY scoping: app → KBs
          if not kb_ids: return None   (fail-closed, answer.py:70 — good pattern to copy)
          opensearch.search_unified(..., knowledge_base_ids=kb_ids)
     ─► llm.generate_rag_answer(query, chunks, model, system_prompt)
     ─► response                                              [I7] no trace, no audit record at all
```

`answer.py` already treats an empty filter as dangerous. Its comment at lines
66–69 notes that an empty list means *unscoped* on every search path, and
returns `None` instead. Any permission filter must keep this **fail-closed**
property: a user with no permitted KBs must get nothing, not everything.

### 1c. Agentic `POST /api/v1/ask-agentic` (`routers/agentic_ask.py:8`)

```
ask_agentic ─► AgenticRAGService.ask(query, model)      user_id defaults to "api_user" (agentic_rag.py:164)
            ─► LangGraph: guardrail → retrieve → tool_retrieve → grade → (rewrite ↺) → generate
            ─► tools.create_retriever_tool → opensearch.search_unified(query, embedding, size, use_hybrid)
                                                         [I11] NO knowledge_base_ids: whole index
```

The retriever tool is built once per service instance in
`services/agents/factory.py`. Per-request scoping needs either a per-request
tool or KB IDs carried on the runtime `Context` (`agents/context.py`).

### 1d. `POST /api/v1/hybrid-search/` (`routers/hybrid_search.py:12`)

This is raw chunk search with no KB filter [I12]. It returns `chunk_text` for
any chunk in the index. With permissions in place, this endpoint would be a
direct bypass unless it is filtered too.

### Insertion point summary

| ID | Location | What could be checked | Today |
|----|----------|-----------------------|-------|
| I1 | `main.py:116` (middleware stack) | Token validation, request ID, source IP capture | CORS only |
| I2 | Every router signature | `CurrentPrincipalDep` + role requirement | Nothing |
| I3 | `routers/ask.py:99,191`; `agentic_rag.py:164` | Pass the real actor into tracing | `"api_user"` literal |
| I4 | `services/cache/client.py:22` | Include the caller's effective KB/ACL scope in the key | Key = query/model/top_k/hybrid/categories. Once permissions exist, a cached answer from a privileged user would be served to anyone. |
| I5 | `routers/ask.py:42` | Filter to permitted KBs | Unscoped |
| I6 | `routers/ask.py:147` | Restrict which models a role may request | Caller picks any model |
| I7 | End of every answer path | Write an audit record | Langfuse only (legacy/agentic), nothing on the app path |
| I8 | `routers/chat_completions.py:93` | Key → application binding | Any slug is callable |
| I9 | `routers/chat_completions.py:61` | Reject missing, unknown or revoked keys, and keys issued for a different app | Stamps `last_used_at` only |
| I10 | `services/applications/answer.py:64` | Intersect app KBs with the caller's permitted KBs (and document-level ACL tags) | App KBs only |
| I11 | `services/agents/tools.py:47` | Same filter as I10 | Unscoped |
| I12 | `routers/hybrid_search.py:34` | Same filter as I10 | Unscoped |
| I13 | Admin CRUD routers (`knowledge_bases`, `documents`, `applications`, `providers`, `retrieval`, `api_keys`, `system`) | Admin role | Open |
| I14 | `routers/agentic_ask.py:63` (`/feedback`) | Only the trace's actor may score it | Anyone can score any trace ID |

---

## 2. Auth surface today

| Mechanism | Where | Status |
|-----------|-------|--------|
| HTTP auth middleware | none | **Absent.** The only middleware is `CORSMiddleware` (`main.py:116`), with `allow_credentials=True` and origins from `CORS_ALLOW_ORIGINS`. |
| Auth dependency | `dependencies.py` | **Absent.** All deps are service handles (`SessionDep`, `OpenSearchDep`, …). There is no request-identity dependency. |
| `src/middlewares.py` | `middlewares.py` | Two unused logging helpers (`log_request`, `log_error`). The file's own comment says they are "not integrated". Nothing imports them. |
| Per-application API keys | `models/api_key.py`, `repositories/api_key.py`, `routers/api_keys.py` | Keys are minted as `sk-<token_urlsafe(32)>` and stored as SHA-256 hashes, with a display prefix and last4, and with revoke and rotate. **Not enforced** (`routers/chat_completions.py:12-15`, `_record_key_usage`). The admin UI says so plainly (`frontend/src/app/applications/[id]/EndpointPanel.tsx:198`). Even when a key is presented, `key.application_id` is never compared with the requested application. |
| Provider credentials | `config.py:47,68` (`LLM__API_KEY`, `VLM__API_KEY`); `ModelProvider.api_key_ref` | Outbound credentials to model servers. These don't authenticate callers. |
| Infra-level auth | `compose.yml` | OpenSearch runs with `DISABLE_SECURITY_PLUGIN=true` (l.87) and Dashboards with `DISABLE_SECURITY_DASHBOARDS_PLUGIN=true` (l.115). The app Redis has no password. Ollama is unauthenticated. **Every service publishes its port on `0.0.0.0`**, including Postgres, OpenSearch (9200), Redis and Ollama. API-level RBAC is therefore bypassable by anyone who can reach those ports. The perimeter story needs these bound to `127.0.0.1`, or left unpublished, in a hardened profile. |

**Reusable pieces:** the API-key model is sound (hash-only storage, one-time
plaintext, rotate and revoke). Enforcement is roughly a 20-line change behind a
flag. It is the natural *machine-to-machine* credential alongside an
*end-user* identity token from the host application's SSO.

---

## 3. Data model

Tables are created by `Base.metadata.create_all`
(`db/interfaces/postgresql.py:60`). **There is no Alembic directory and there
are no migrations.** `create_all` creates missing tables but never alters
existing ones.

| Table | Owner / tenant / ACL fields | Notes |
|-------|-----------------------------|-------|
| `knowledge_bases` | **None** | `extra_metadata` JSON exists but nothing reads it. The docstring claims "each knowledge base maps to a dedicated OpenSearch index". In reality `index_name` is computed on create (`routers/knowledge_bases.py:46`) but **never used**: every KB shares the single `rag-documents-chunks` index and isolation relies entirely on the `knowledge_base_id` term filter. |
| `documents` | **None** | `extra_metadata` JSON exists. There is no classification or sensitivity label. |
| `document_chunks` | **None** | It does carry `knowledge_base_id`. |
| `rag_applications` | **None** | Many-to-many with KBs via `application_knowledge_bases`. There is no "who may use this app". |
| `application_api_keys` | Bound to `application_id` | The only principal-like entity. It has no role or scope. |
| `model_providers`, `model_configurations`, `retrieval_configurations`, `ingestion_jobs` | **None** | — |
| users / roles / groups / audit | **Don't exist** | — |

The OpenSearch chunk mapping (`services/opensearch/index_config_hybrid.py`) is
`"dynamic": "strict"`. It has `knowledge_base_id` and `document_id` keyword
fields but no ACL field. The index uses the **nmslib** kNN engine, so filters
are *post-filters*: the client oversamples k by 10× to compensate
(`opensearch/client.py:16-33`). That's acceptable for coarse KB filters. It
gets worse with fine-grained per-user ACLs, which is a strong argument for
**KB-level permissions first** and document-level tags second.

**Migration consequence:** RBAC and audit need *new* tables (`principals` /
`roles`, `knowledge_base_grants`, `audit_events`). `create_all` will create
those on existing deployments without Alembic, which keeps them additive.
Adding *columns* to existing tables (for example `documents.acl_tags`) is
**not** possible with `create_all` alone. Either introduce Alembic with a
baseline revision, or keep all new data in new side tables. Chunk-level ACL
tags in OpenSearch need a `PUT _mapping` to add the field, which is additive
and allowed on a strict mapping. Existing chunks lack the field and must be
treated as "KB-level permission only" rather than failing open.

---

## 4. Logging today

### What Langfuse captures

On the **legacy path**, via `services/langfuse/tracer.py` (`RAGTracer`):

| Span | Input | Output |
|------|-------|--------|
| trace (`trace_rag_request`) | `query`, `user_id="api_user"`, `session_id="session_api_user"` | `answer`, duration, length |
| `query_embedding` | query, length | duration, success |
| `search_retrieval` | query, top_k | chunk count, unique doc count, total hits, **document IDs** (not chunk IDs, not scores) |
| `prompt_construction` | chunk count | prompt length, 200-char preview |
| `llm_generation` | model, **full prompt** (retrieved text included) | full response, model |

On the **agentic path**, the LangGraph callback handler plus per-node spans
(`agentic_rag.py:189-266`) are also attributed to `"api_user"`.

The **application path** (`answer.py`, playground, `/v1/chat/completions`) has
**no tracing**. The routers and the service never receive a `LangfuseDep`.

### Why this isn't a compliance audit trail

| Requirement | Status |
|-------------|--------|
| Actor identity | Always `"api_user"`. Nothing to attribute to. |
| Authenticated principal / role / tenant at decision time | Absent |
| Timestamp | Langfuse-side only, not in our DB |
| Source IP / user agent / request ID | Not captured anywhere (`request.client` is never read) |
| Which application / API key was used | Not captured |
| KB scope applied (the permission decision) | Not captured |
| **Retrieved chunk IDs + scores** | Not captured. Only document IDs, only on the legacy path. |
| Final answer | Captured (legacy/agentic only) |
| Cache hit vs. fresh generation | Not captured. A cache hit returns *before* any search span, so the record doesn't show which chunks the cached answer came from. |
| Redactions / guardrail decision | The agentic guardrail score sits in trace metadata only. Nothing covers other paths. |
| Durability | `LANGFUSE__ENABLED` can be off. Sending is async and batched (`flush_at`/`flush_interval`), and every tracing error is swallowed (`tracer.py:121`, `chat_completions.py:78`). **Loss is silent by design**, which is right for observability and wrong for audit. |
| Queryable / exportable by compliance, in our system | No. Data lives in Langfuse's ClickHouse, behind Langfuse's own UI and auth, with a retention and schema we don't control. |
| Tamper evidence | None |
| Admin actions audited (KB/doc/app/key create/delete, key rotation) | None. Only question answering is traced at all. |

Application logging is `logging.basicConfig` to stdout (`main.py:34`), which is
unstructured. Some logs include query text (`routers/hybrid_search.py:32`,
`agents/tools.py:37`), which may itself be a data-handling concern for
regulated buyers.

**Conclusion:** audit logging needs its own synchronous write path to Postgres
(an `audit_events` table), written in the same request as the answer, with
Langfuse kept as the optional observability layer. The application answer path
is the priority, because it is currently completely unrecorded.

---

## 5. Frontend

- Next.js App Router, with sections `applications`, `knowledge-bases`,
  `documents`, `providers`, `retrieval` and `system`. The sidebar title is
  "Admin Control Plane".
- **No auth of any kind:** no `middleware.ts`, no login page, no session or
  token storage, no user or role display, no logout.
- `src/lib/api/client.ts` sends requests with no `Authorization` header and
  no credentials. The one exception is the playground's optional API-key field,
  which sets `Authorization: Bearer` for `/v1/chat/completions`
  (`resources.ts:93`).
- `NEXT_PUBLIC_API_URL` is baked in at build time, so the browser calls the API
  directly. There is no BFF or proxy layer where a server-side session could be
  held.
- **Good seam:** every call goes through `apiGet` / `apiSend` / `apiUpload`
  in `client.ts`. Attaching a bearer token, whether from a host-app-provided
  header, a cookie, or an OIDC client library, is a single-file change.
  Handling `401`/`403` is equally central (`ApiError.status`).

---

## 6. Other findings relevant to later phases

- **`GET /api/v1/system/gpu` already exists** (`routers/system.py:170`). That
  is relevant to Phase 2b's "GPU detection in admin UI". Assess what it reports
  before planning new work.
- **Provider `test` and `models` endpoints make the backend call admin-supplied
  URLs** (`routers/providers.py:75,85`). Behind RBAC these must be admin-only.
  Without RBAC, anyone who can reach the API can point the backend at arbitrary
  internal hosts, which is SSRF.
- The legacy `/ask` "no results" message still says "papers"
  (`routers/ask.py:123`), a leftover from the arXiv template. Cosmetic.
- There's no request-ID middleware, so an audit record, a Langfuse trace and a
  log line can't currently be correlated. Adding one is cheap and would be the
  first building block of I1.
