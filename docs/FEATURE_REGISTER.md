# Enterprise-LOCAL-RAG: Feature Register (code-verified)

**Purpose.** A code-verified baseline against the Open WebUI comparison. It records what is actually implemented, what is incomplete, and what is deliberately out of scope.

**Verified at:** branch `feature/owui-gap-analysis`, commit `4ce0a6b`, working tree clean at audit start.
**Companion docs:** [`CODEBASE_AUDIT.md`](CODEBASE_AUDIT.md) (architecture, routes, baseline) · [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) (ordered plan) · [`decisions/`](decisions/) (ADRs).

> **Headline.** The comparison document is stale. It described the repository *before* the security track shipped. RBAC, JWT/trusted-header identity, API-key enforcement, knowledge-base grants, a structured audit log and a hardened compose overlay are all built and tested. What remains is mostly the **Hardware/Backend track** (GPU, non-Ollama backends on every path, embedding dimension) plus smaller gaps listed below.

## 1. Labels and evidence rules

`VERIFIED — COMPLETE` · `VERIFIED — PARTIAL` · `VERIFIED — MISSING` · `NOT REQUIRED — INTENTIONAL` · `BLOCKED / DECISION REQUIRED`. (`UNVERIFIED` is not used: every row below was inspected.)

Evidence kinds used in this file:

- **Test** = an automated test in the offline suite (`cd backend && make test`-equivalent). Baseline: **529 passed**, 0 failed, with the `tests/integration/` suite not run (it needs live services).
- **Live** = a manual check against the real Docker stack on 2026-11-11: JWT mode with a locally generated RSA key, `API_KEYS__ENFORCE=true`, `AUDIT__LEVEL=metadata`, using real ingested documents. Script and keys were kept outside the repo. 44 checks were run, and the 6 reported as failing were a bug in the throwaway script's tuple comparison. The values shown were the expected ones. Real behavioural differences are recorded in the rows below.
- **Code** = read and traced, but not exercised at runtime.
- **BLOCKED** = could not be verified here, with the reason.

No secrets, tokens or customer data are recorded in this file.

## 2. Summary matrix

| # | Feature | Comparison claim | Verified status | Decision |
|---|---------|------------------|-----------------|----------|
| 1 | Hybrid search (BM25 + kNN + RRF) | Present | **COMPLETE** (RRF only; no cross-encoder reranker). One engine caveat (nmslib post-filter) | Preserve; add regression/benchmark tests |
| 2 | Agentic retrieval (LangGraph) | Present | **COMPLETE** — works, tested, and (Phase 4) runs on the configured `LLM__PROVIDER`; verified live on native Ollama, Ollama `/v1` and llama.cpp. Answer quality depends on model size (a 1B model's grader behaved differently under llama.cpp) | Preserve; add a recall/behaviour benchmark with a larger model |
| 3 | Docker/Compose deployment | Present | **COMPLETE** (API stack up on this machine; hardened overlay validates) | Preserve |
| 4 | Ad-hoc chat file/image attachment | Partial | **MISSING** (no chat attachment path at all) | Defer; later cycle |
| 5 | Extraction engines / OCR fallback | Partial (Docling only) | **PARTIAL** — Docling only, `do_ocr` is a config bool defaulting to off, and no test for scanned PDFs. VLM provider exists but is unused by ingestion | Defer; only if tests show a gap |
| 6 | Per-KB access control | Partial; legacy routes may search all KBs | **COMPLETE** for retrieval (legacy routes included), with hardening items (see §3.6) | Preserve; hardening pass |
| 7 | Model presets / applications | Partial | **PARTIAL** — applications bundle KBs, prompt, retrieval config; but per-application provider/endpoint is stored and **not honoured**, and `rag_strategy="agentic"` is stored but not run by the application path | Implement H2a (approved subset only) |
| 8 | RBAC | Partial / S2 | **COMPLETE** for API routes (3 roles, per-router policy, policy test). No route-level resource roles inside a KB (grants are retrieval-only) | Preserve |
| 9 | SSO / OIDC / LDAP | Partial / S1 | **OIDC/JWT/trusted-header: COMPLETE** (validates the buyer's IdP tokens; admin UI has OIDC sign-in). **LDAP: not implemented** | LDAP: **BLOCKED / DECISION REQUIRED** |
| 10 | API-key enforcement | Partial / S2a | **PARTIAL** — issue/hash/revoke/rotate and enforcement are done. **No expiry, scopes or rate limiting** | Implement (small) if buyers need it |
| 11 | Structured audit log | Partial / S4 | **PARTIAL** — query, admin, denial, export and purge events with retention and export are built. **No tamper-evidence** beyond "append-only by convention" | Implement hardening (optional) |
| 12 | OpenTelemetry export | Partial | **MISSING** (Langfuse only; no OTel dependency or config) | Defer until a concrete requirement |
| 13 | Multiple inference backends | Partial | **PARTIAL** — Ollama `/v1` and llama.cpp **verified live on every answer path** (2026-11-11, Phases 3–4); vLLM/TGI contract-tested only (**BLOCKED**, no GPU host). Per-application provider/endpoint still ignored | H1, H2 done; H2a next |
| 14 | Quantized model formats | Partial | **PARTIAL** — GGUF verified (Ollama and llama.cpp). AWQ/GPTQ/FP8 documented but **unverified** (needs vLLM/TGI run) | H4 docs done; verify on a GPU host |
| 15 | NVIDIA GPU support | Missing / broken | **MISSING** — no compose GPU reservation anywhere; `/system/gpu` runs `nvidia-smi` inside the *API* container, so it cannot report Ollama's GPU use | Implement H3 |
| 16 | Embedding dimension from config | (not in comparison) | **IMPLEMENTED** — the mapping is built from `EMBEDDING__DIMENSION`; `OPENSEARCH__VECTOR_DIMENSION` is deprecated and warns; a stale index sets an ingestion `409` guard | None (reindex command is a later ticket) |
| 17 | Hardened compose overlay (S5) | (not in comparison) | **COMPLETE** (config validates; not run live in this audit) | Preserve |
| 18 | Document-level ACLs (S3a) | (not in comparison) | **MISSING**; blocked on the kNN-engine decision | Defer |

Not required (kept out): see §5.

## 3. Detail per feature

### 3.1 Hybrid search: COMPLETE

- **Paths:** `backend/src/services/opensearch/{client.py,query_builder.py,index_config_hybrid.py}`; `routers/hybrid_search.py`.
- **How it works:** a BM25 + kNN `hybrid` query is fused by an OpenSearch search pipeline using **RRF** (`hybrid-rrf-pipeline`, `index_config_hybrid.py`). No cross-encoder or other reranker exists (`grep rerank` returns nothing).
- **Evidence:** Test: `test_opensearch_query_builder.py`, `test_opensearch_kb_scoping.py`, `test_opensearch_empty_scope.py`, `api/routers/test_hybrid_search.py`. Live: BM25 search returned the expected KB's chunk after real ingestion.
- **Caveat:** the kNN field uses the `nmslib` engine, so KB filters are **post-filters**. A narrowly scoped search can return fewer results than exist. This is mitigated with 10× oversampling (`KNN_FILTER_OVERSAMPLE`, `client.py`). It cannot leak data, but it can under-return. See ADR-0004.
- **Not verified:** the vector/RRF leg ran only in unit tests with mocks. The live check used `use_hybrid=false`. Retrieval quality has no benchmark.
- **Acceptance for "proven":** a regression test that seeds known chunks and asserts the RRF ordering, and a small recall benchmark. No code change needed.

### 3.2 Agentic retrieval: PARTIAL

- **Paths:** `services/agents/agentic_rag.py` (graph), `services/agents/nodes/{guardrail,retrieve,grade_documents,rewrite_query,generate_answer,out_of_scope}_node.py`, `routers/agentic_ask.py`.
- **Works:** guardrail → retrieve → grade → rewrite loop → answer, plus an `out_of_scope` exit. Scoped to the caller's KBs. A live check with a no-grant user returned a refusal message with `chunks_used = 0` and an explanatory reasoning step.
- **Evidence:** Test: `tests/unit/services/agents/{test_agentic_rag,test_nodes,test_tools,test_models}.py`, `test_agentic_scope.py`, `api/routers/test_agentic_ask.py`. The baseline run emitted an un-awaited-coroutine `RuntimeWarning` from `test_nodes.py::TestGenerateAnswerNode` (pre-existing, tests still pass).
- **Gap (closed in Phase 4):** the four LLM-using nodes used to call `ollama_client.get_langchain_model(...)` directly. They now call `runtime.context.chat_model_source`, which is the configured `LLMProvider` (falling back to the Ollama client only when no provider is supplied). Live evidence is in §3.13.
- **Remaining caveat:** the agent's behaviour depends on the model. With `llama3.2:1b`, llama.cpp's grader judged the test document not relevant in every run (3 of 3, then 4 of 4 including an earlier run) while Ollama accepted it after one rewrite. The parse succeeded each time; the nodes' fallbacks only fire on errors. Treat 1B models as a smoke test, not a quality bar.
- **Not verified:** actual rewrite/grading quality against a real model (needs a model; `llama3.2:1b` is tiny).

### 3.3 Docker / Compose: COMPLETE

- **Paths:** `compose.yml`, `compose.localports.yml` (machine-specific override, untracked upstream by design), `compose.hardened.yml`, `Makefile`.
- **Live:** with `compose.yml` + `compose.localports.yml`, `api`, `postgres`, `opensearch`, `redis` and `ollama` started and `GET /api/v1/health` reported database, OpenSearch and Ollama healthy. The frontend image was not started. The Langfuse stack was not started (`LANGFUSE__ENABLED=false` for the audit).
- **Static:** `docker compose -f compose.yml -f compose.hardened.yml config -q` exits 0.
- **BLOCKED:** a clean-clone, from-scratch `make start` of the *full* stack (the existing named volumes and images were reused, deliberately, to avoid deleting the operator's data).

### 3.4 Chat attachments: MISSING

- `ChatMessage.content` is `str` (`schemas/api/domain.py:441-443`), so `/v1/chat/completions` cannot accept image or file parts. The only upload route is the admin KB document upload (`routers/documents.py`).
- Prerequisites before building: a temp-file lifecycle (the current upload writes to `tempfile.gettempdir()/rag-uploads` with **no size cap, no type allowlist and no cleanup**), a supported-types decision, and the VLM question (the VLM provider is created at startup but is not used by the ingestion pipeline).
- **Decision:** defer; needs product input (§ roadmap, Phase 9).

### 3.5 Extraction / OCR: PARTIAL

- **Paths:** `services/pdf_parser/{docling.py,factory.py,parser.py}`; `config.py` `PDFParserSettings.do_ocr: bool = False`. `.md`/`.txt` are decoded directly; every other type is handed to the PDF/Docling parser.
- **Works:** text and markdown ingestion end-to-end (live: two documents reached `COMPLETED`). Docling parsing is covered by `test_pdf_parser.py` (mocked).
- **Gaps:** no scanned-PDF or image-heavy test, no fallback engine. OCR is a single boolean in the same engine.
- **Decision:** defer. Add a scanned-PDF fixture and measure first. Only add a fallback if a real coverage gap is shown.

### 3.6 Per-KB access control: COMPLETE (with hardening items)

- **Paths:** `models/access.py` (grants), `repositories/grants.py`, `services/access/scope.py` (single resolver), call sites in `routers/{ask,agentic_ask,hybrid_search,chat_completions}.py` and `services/applications/answer.py`, `services/cache/client.py` (scope in cache key), `services/opensearch/{client,query_builder}.py` (empty scope matches nothing).
- **Legacy endpoints covered:** `/api/v1/ask`, `/stream`, `/hybrid-search`, `/ask-agentic`.
- **Evidence:** Test: `test_retrieval_scope.py`, `test_agentic_scope.py`, `test_application_answer_scope.py`, `test_opensearch_empty_scope.py`, `api/routers/test_legacy_scope.py`, `test_grants.py`. **Live** (real chunks in two KBs, grants for groups `g-a`/`g-b`): a `g-a` caller saw only KB-A chunks; a `g-b` caller never saw KB-A chunks; a user with no grants saw none of either; `/ask` returned `chunks_used=0` for the no-grant and wrong-group callers; `/ask-agentic` refused for a no-grant caller.
- **Behaviours to be aware of (by design):**
  - A valid application API key is *unrestricted* at the scope layer and is limited by the application's attached KBs (`scope.py`, `api_key` branch).
  - `AUTH__ADMIN_BYPASS_RETRIEVAL=false` by default: the admin role alone does not widen retrieval, but admin does imply the querier role (an admin can call `/ask` and is then subject to grants).
  - `AUTH__UNGRANTED_KB_ACCESS=deny` by default (`allow` is a migration aid).
  - With `AUTH__MODE=disabled` (the default), everything is open. This is the shipped default and must be called out in deployment docs.
- **Remaining hardening (not blockers):** (a) a regression test that walks every retrieval-capable route and asserts a scope is applied (`test_route_policy.py` checks roles, not scope); (b) background ingestion jobs are admin-only and not caller-scoped (acceptable, but undocumented); (c) the engine post-filter caveat in §3.1.

### 3.7 Model presets / applications: PARTIAL

- **Paths:** `models/application.py`, `routers/applications.py`, `services/applications/answer.py`, `frontend/src/app/applications/*`.
- **Works:** an application bundles KBs, system prompt, model name, retrieval config, streaming flag, and API keys. It is used identically by the playground and `/v1/chat/completions`.
- **Gaps (verified in code):**
  1. `answer.py` reads only `model_cfg.llm_model`. `llm_provider` and `llm_endpoint` are stored and shown in the UI but the global `LLMProvider` is always used.
  2. `rag_strategy="agentic"` is stored, but the application path always runs the traditional pipeline.
- **Decision:** implement only (1) and (2) from the old plan (H2a). No persona system, marketplace, or templates.

### 3.8 RBAC: COMPLETE

- **Paths:** `src/auth/{principal,dependencies,authenticators,completions}.py`; the policy matrix is in `src/main.py` (`QUERIER`, `ADMIN`, `AUDITOR` dependencies applied once per router).
- **Roles:** `admin` (implies `querier`, and `auditor` unless `AUTH__ADMIN_CAN_READ_AUDIT=false`), `querier`, `auditor`. Mapped from IdP claim values.
- **Evidence:** Test: `test_route_policy.py` (fails if any route lacks a policy), `api/routers/test_rbac.py`, `test_audit_roles.py`, `unit/auth/*`. **Live**: all 12 probed query/admin/audit routes returned 401 unauthenticated; with a querier or auditor token every admin route returned 403; querier on audit returned 403; auditor and admin on audit returned 200. Expired, wrong-audience and forged HS256 tokens all returned 401.
- **Observations:**
  - A token that maps to **no** platform role is rejected with **401**, not 403, including on `/auth/me`. This is the existing authenticator behaviour, not a defect, but it is not what the route docs imply.
  - `GET /api/v1/health` and `POST /v1/chat/completions` (authorised in the handler) are the only exemptions.
  - There is no per-KB admin or editor role. KB grants are read/retrieval only (`permission` column), and admin routes are all-or-nothing.
- **Decision:** preserve. Resource-level write roles only if a buyer asks.

### 3.9 SSO / OIDC / LDAP

- **JWT/OIDC: COMPLETE.** `AuthSettings.mode` ∈ `disabled|jwt|trusted_header`. JWT validation uses JWKS (`AUTH__JWKS_URL`) or a static PEM (`AUTH__PUBLIC_KEY_PATH`, air-gapped), checks issuer, audience and expiry, and maps claims to roles and groups. The platform does not issue sessions. The admin UI performs the OIDC sign-in (`NEXT_PUBLIC_AUTH_MODE=oidc`, `frontend/src/app/auth/callback`). Evidence: Test `unit/auth/test_jwt_authenticator.py`, `test_trusted_header_authenticator.py`; Live: JWT path with a static PEM, expired/forged/wrong-aud rejected.
- **Not verified:** JWKS fetch against a real IdP, and the browser OIDC flow (BLOCKED: no IdP available).
- **LDAP: not implemented** (no `ldap` code anywhere). **BLOCKED / DECISION REQUIRED.** Is it wanted at all? Most buyers federate via their IdP, which already fronts LDAP. See ADR-0001.
- **Token lifecycle:** no refresh or revocation is needed on the platform side (access tokens are short-lived and validated each request). There is no revocation list.

### 3.10 API-key enforcement: PARTIAL

- **Paths:** `models/api_key.py`, `routers/api_keys.py`, `auth/completions.py`; setting `API_KEYS__ENFORCE`.
- **Works:** per-application keys (`sk-…`), stored as SHA-256 only, shown once, rotate and revoke endpoints (admin), usage `last_used_at`. With `API_KEYS__ENFORCE=true`, `/v1/chat/completions` rejects missing, garbage and wrong-application keys with an OpenAI-shaped 401. Live: unauthenticated and garbage-key requests returned 401. Test: `api/routers/test_chat_completions_auth.py`.
- **Missing:** `expires_at`, scopes, per-key rate limits (the model has `created_at`, `last_used_at`, `revoked_at` only). Enforcement is **off by default** (`enforce=false`); keys are then recorded but not required.
- **Not verified live:** a *valid* key round trip (not exercised in this audit; covered by tests).
- **Decision:** implement expiry first (small, no scopes needed). Scopes and rate limits only on demand.

### 3.11 Audit log: PARTIAL

- **Paths:** `models/audit.py` (`audit_events`), `services/audit/{recorder,events,factory}.py`, `middleware/audit.py`, `routers/audit.py`, `cli/audit_purge.py` (`make audit-purge`), `frontend/src/app/audit`.
- **Works:** actions `query.answer`, `query.search`, `admin.mutation`, `access.denied`, `audit.export`, `audit.purge`. Events carry actor, roles, groups, auth method, API key and app IDs, IP, route, request ID, requested/permitted/dropped KB IDs, retrieved chunk IDs, and SHA-256 digests of query and answer (text only at `AUDIT__LEVEL=full`). Cursor-paged list, detail, status, NDJSON/CSV export. Retention is a manual purge. `fail_mode=log|deny`. Live: with `metadata` level, the log contained `access.denied`, `admin.mutation`, `audit.export`, `query.answer` and `query.search` after the smoke run, and the auditor could read it.
- **Gaps:** (1) append-only is by convention only. There is no DB-level protection, no hash chain and no signed export, so a DB admin can alter rows. (2) Default level is `off`. (3) Purge is manual; no scheduler (by design, "nothing is ever purged automatically"). (4) The agentic path builds its own Langfuse traces and does not carry the request ID.
- **Decision:** hardening is optional and depends on the buyer's compliance bar (tamper-evidence); see roadmap Phase 6.
- **Note:** the live run wrote metadata-level events into the existing dev database (names/IDs only, no secrets). They remain, since audit rows are append-only.

### 3.12 OpenTelemetry: MISSING

- No OTel dependency, exporter or config (`grep` in `pyproject.toml`, `src/`, `compose.yml`). Langfuse tracing exists (`services/langfuse/`, `RAGTracer`, caller attribution). **Decision:** defer; add only for a concrete interoperability requirement.

### 3.13 Multiple inference backends: PARTIAL

- **Paths:** `services/providers/llm/{base,factory,openai_compatible_provider}.py`, `services/ollama/client.py` (`OllamaClient` implements `LLMProvider`), `routers/providers.py`.
- **Works:** `LLM__PROVIDER=ollama|openai|openai-compatible|vllm|nim|sglang|localai`. After Phase 4 every answer path uses `LLMProviderDep`: `/ask`, `/stream`, `/ask-agentic`, the application path (`/v1/chat/completions` and the playground), plus `/api/v1/health` (`llm` entry) and `/api/v1/system/health`.
- **Still on Ollama directly:** the Ollama-type branch of `routers/providers.py` `/test`, the `ollama` entry in `routers/ping.py` (reported, and degrades the service only if the LLM or embeddings use Ollama), and Ollama-based embeddings/VLM. Per-application provider/endpoint fields remain ignored (Phase 5: H2a).
- **Evidence (Phases 3–4, 2026-11-11):**
  - Test: `unit/services/providers/test_providers.py`, `test_openai_compatible_contract.py` (25 mocked-transport tests), `unit/services/agents/test_provider_routing.py` (16 tests: the four real nodes against an OpenAI-compatible backend with Ollama rigged to fail; structured-output methods; wire format `max_tokens`; no network beyond the endpoint) and `api/routers/test_provider_routing.py` (11 tests: `/ask` and `/stream` use the provider, default model follows the provider, health reporting). Existing tests unmodified; one API-conftest fixture now shares one mock between the Ollama client and the provider.
  - **Live, verified:** Ollama `/v1` and llama.cpp `llama-server` (GGUF) each pass `check_llm_backend` and a full API run: ingest, `/ask`, `/stream`, `/ask-agentic`, `/v1/chat/completions`. With llama.cpp, Ollama's chat-request count was unchanged across the run, so generation really went to llama.cpp. Structured calls: 15/15 with `json_schema` on both.
  - **BLOCKED:** vLLM and TGI (no NVIDIA host). Contract-tested only. `compose.vllm.yml` is an untested template.
  - Supported-backend list and caveats: [`deployment/inference-backends.md`](deployment/inference-backends.md).
- **Phase 3 changes:** `OpenAICompatibleLLMProvider` sends the system prompt as a `system` message, always sends `max_tokens` (`LLM__MAX_TOKENS`, default 1024), treats a 404 on `/models` as "reachable", raises `LLMProviderError` on error payloads instead of yielding an empty answer, and tolerates null content.
- **Phase 4 changes:** `LLMProvider.get_langchain_model()` (non-abstract); `OpenAICompatibleLLMProvider` returns a `ChatOpenAI` bound to its explicit base URL, key and `max_tokens` (sent as the classic `max_tokens` via `extra_body`, because `langchain-openai` otherwise sends `max_completion_tokens`, which older servers ignore); `LLM__STRUCTURED_OUTPUT_METHOD` (default `json_schema`, chosen from live results; the plan's `json_mode` default failed every rewrite); `Context.llm_provider` + `chat_model_source`; `/ask` and `/stream` use `LLMProviderDep`; `AskRequest.model` defaults to the active provider's model (`Settings.default_llm_model`); `/api/v1/health` gains an `llm` entry; `/api/v1/system/health` no longer reports an unhealthy provider as healthy (it tested the truthiness of the returned dict). New dependency: `langchain-openai` (the lock also moved `langchain-core`, `openai`, `langsmith` and `certifi` forward).
- **Decision:** H1 and H2 done for the verifiable backends. Next: H2a (per-application provider), or the GPU overlay if an NVIDIA host is available.

### 3.14 Quantized formats: PARTIAL

- **GGUF: verified.** A GGUF model loaded and answered through both Ollama and `llama-server` on 2026-11-11.
- **AWQ/GPTQ/FP8: unverified.** They depend on a vLLM/TGI run that has not been done. The docs mark them unverified and give the vendor flags. Do not claim support before a vLLM/TGI row is verified.

### 3.15 NVIDIA GPU: MISSING

- **Paths:** `compose.yml` (no `deploy.resources.reservations` or `runtime: nvidia` on `ollama`), `routers/system.py` `gpu_info()` (`shutil.which("nvidia-smi")` inside the API container).
- **Verified:** neither the `api` nor the `ollama` service reserves a GPU, so a Linux GPU host would run inference on CPU, and the endpoint probes the wrong container (it reports "no NVIDIA driver detected" in the API container). The endpoint cannot show whether Ollama is on GPU.
- **BLOCKED:** actual GPU inference verification needs an NVIDIA host. This development machine is macOS and Docker containers cannot use the Apple GPU.
- **Decision:** implement H3 (overlay + Ollama `/api/ps` residency probe). Code can be validated with `docker compose config` here. Runtime proof is BLOCKED until a GPU host is available.

### 3.16 Embedding dimension: PARTIAL (defect)

- `index_config_hybrid.py:41` hardcodes `"dimension": 1024`. `EMBEDDING__DIMENSION` is used by KB creation defaults but not by the index mapping, and `OPENSEARCH__VECTOR_DIMENSION` (`config.py:135`) is unused. Changing the embedding model to a non-1024 model fails at indexing. **Decision:** implement H5 (it is small and unblocks other embedding backends).

### 3.17 Hardened overlay: COMPLETE

- `compose.hardened.yml` removes host ports from data stores and rebinds API/UI/Langfuse to `RAG_BIND_ADDRESS` (default `127.0.0.1`); `make start-hardened`, `make health-hardened`. Config validates. **BLOCKED:** not brought up live in this audit (it would conflict with the port remap used here); the "closed ports" claim is code-verified only. Residual: OpenSearch and Redis run without authentication *inside* the network (documented in the README).

### 3.18 Document-level ACLs (S3a): MISSING

- Designed (side table + `acl_tags` keyword on chunks), but blocked on the kNN-engine decision (nmslib → lucene/faiss needs a reindex). Defer.

### 3.19 Other things found while auditing (not in the comparison)

| Finding | Evidence | Severity | Note |
|---|---|---|---|
| Upload path writes `<checksum>-<client filename>` under the temp dir with no size cap, type allowlist or cleanup | `routers/documents.py` `upload_document` | Low–Med (admin-only route) | A client filename containing a path separator is not sanitised. Fold into the attachments/ingestion work |
| `ruff format --check` flags 45 files | baseline | Low | Pre-existing; `make lint` (`ruff check` + mypy) passes |
| Frontend `npm run lint` is unconfigured (it opens an interactive ESLint wizard) | baseline | Low | Type-check via `npm run build` passes |
| `NEXT_PUBLIC_*` are baked at build time | `CLAUDE.md`, compose | Info | Matters for the hardened overlay |
| `nvidia-smi` via `subprocess.run` with fixed arguments | `routers/system.py` | Info | The only subprocess in `src/`. No `eval`/`exec`/`pickle`/dynamic import anywhere in `backend/src` |

## 4. Existing strengths to preserve (verified)

| Strength | Status |
|---|---|
| Agentic RAG (rewrite/grade/guardrail) | Present and unit-tested; live quality not measured |
| Structured audit logging | Present; see §3.11 for hardening |
| No arbitrary Python plugin execution | **Confirmed**: no `eval`, `exec`, `pickle`, `importlib`, `__import__` or `os.system` in `backend/src`. The sole `subprocess.run` calls `nvidia-smi` with constant arguments. The provider `/test` and `/models` endpoints make server-side HTTP calls to *admin-supplied URLs* (an SSRF-class surface, admin-only, no allowlist) |
| OpenSearch hybrid retrieval | Present; no recall benchmark yet |

## 5. Not required, kept out (no work planned)

Web search; browser/terminal code execution; persistent cross-chat memory; conversation folders/tags/pins; voice/video; image generation; scheduled prompts; task management; multi-vector-DB catalogue; full-context (no-chunk) mode; template variables; per-model bound tools; global default capabilities; Notes; Channels; admin-uploaded Python tools/functions; general plugin/pipeline framework; MCP; OpenAPI tool discovery; Markdown skills; slash-commands; model arena; banners; webhooks; standalone pip install; S3/GCS/Azure object storage; Kubernetes/Helm (defer); horizontal replicas (defer); SCIM (revisit if a buyer requires it, not a task).

## 6. Decisions requested (not made by this audit)

1. **LDAP**: needed, or is IdP-only federation sufficient? (Register status: BLOCKED / DECISION REQUIRED.)
2. **First feature to implement** (roadmap recommends Phase 3 / H1, backend verification, because it is verifiable on this machine; H3 GPU first only if an NVIDIA host is available).
3. Whether API-key **expiry** is wanted, and whether scopes/rate limits are ever needed.
4. The **kNN engine** (nmslib → lucene) decision, which gates S3a and affects filtered-search recall.
