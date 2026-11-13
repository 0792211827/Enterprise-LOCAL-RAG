# Enterprise-LOCAL-RAG: Implementation Roadmap

Based on the code-verified [`FEATURE_REGISTER.md`](FEATURE_REGISTER.md) and [`CODEBASE_AUDIT.md`](CODEBASE_AUDIT.md) (commit `4ce0a6b`). **Nothing here is approved for implementation yet.** Each phase is its own Plan Mode → approval → implement → test → update-docs cycle, on its own branch. Do not bundle phases.

**Size:** S ≈ ≤ 2 days · M ≈ 3–7 days · L ≈ 2+ weeks (one engineer, including tests).
**Global rules (carried over from the accepted plan):** every feature ships **off by default** and preserves today's behaviour. A fresh `make start` and `make test` keep working. Existing tests are not rewritten to pass. New persistent structures are **new tables** (no `ALTER`, ADR-0003). Settings follow the `config.py` `__`-prefix style and are documented in `.env.example` and the README in the same change. No new outbound network calls by default. Authorisation is enforced server-side. Unauthorised, wrong-role and cross-KB negative tests are required for anything touching access.

## Status of the original sequence

| Original step (from the register) | Reality at `4ce0a6b` |
|---|---|
| 1 Audit and baseline | **Done** (this document set) |
| 2 Authorization foundation + cross-KB isolation incl. legacy endpoints | **Done** (S2, S3). Hardening in Phase 1 below |
| 3 Identity federation (SSO/OIDC) | **Done** (S1, F1). LDAP is a decision (Phase 7) |
| 4 API-key enforcement | **Done**; expiry/scopes/rate-limit absent (Phase 6) |
| 5 Audit trail | **Done** (S4); tamper-evidence optional (Phase 6) |
| 6 Inference and GPU | **Not started** (H1–H3, H5; Phases 2–5) |
| 7 Model formats | Docs only, after H1 (Phase 3) |
| 8 KB isolation hardening | Phase 1 |
| 9 OCR fallback | Conditional (Phase 8) |
| 10 Chat attachments | Later (Phase 9) |
| 11 Presets/applications | Subset = H2a (Phase 5) |
| 12 OTel | Conditional (Phase 10) |

### Enterprise RAG expansion (`.scratch/enterprise-rag-expansion`), tickets 01 to 10

| Roadmap phase | What landed | Where |
|---|---|---|
| Phase 2 (H5) embedding dimension | **Implemented** (ticket 01): mapping from `EMBEDDING__DIMENSION`, startup guard, ingestion `409` | `services/opensearch/{index_config_hybrid,dimension_guard}.py` |
| Phase 5 (H2a) per-Application endpoint | **Implemented** (tickets 02, 03, 04): provider registry, `env:NAME` key references, `LLM__ALLOWED_ENDPOINT_HOSTS`, agentic strategy on the Application path. ADR-0005's residual-surface note updated | `services/providers/llm/registry.py`, `services/applications/answer.py` |
| Phase 9 ingestion hardening | **Implemented** (ticket 09): upload cap, content-hash stored names, `make uploads-cleanup`. The OCR half of Phase 9 is untouched | `services/ingestion/uploads.py`, `cli/upload_cleanup.py` |
| Phase 10 chat | **Tickets 10 to 13 done**: `/api/v1/chat`, role-gated chat UI, stored Conversations (owner-only, follow-up context), retention (90 days default, daily purge) and privacy defaults (no chat text in audit or Langfuse), ratings with consent-based sharing and Golden set export. No Attachments or rate limits yet (tickets 14 to 18) | `routers/chat.py`, `routers/chat_admin.py`, `services/chat/`, `frontend/src/app/{chat,feedback}` |
| New: eval harness | **Implemented** (tickets 05, 06): Golden sets, recall@k, MRR, groundedness, report compare | `docs/evaluation.md` |
| New: reranker | **Implemented** (ticket 08), on one retrieval seam (ticket 07) | `services/retrieval.py`, `services/rerank/` |

Phase 12 (document-level restrictions) and the lucene reindex remain open; the recall test comes first (ADR-0001).

The order is therefore **revised**: steps 2–5 are complete, so the Hardware/Backend track and a small isolation-hardening pass come first.

## Dependency graph

```
P1 isolation hardening ──────────────────────────────┐ (independent; do first, small)
P2 H5 embedding dimension  (independent)
P3 H1 backend verification ──► P4 H2 all paths via LLMProvider ──► P5 H2a per-app provider
        └──► H4 quantization docs                  (H3 GPU overlay is independent; see below)
P6 H3 GPU overlay + residency probe  (runtime proof needs an NVIDIA host)
P7 API-key expiry / audit tamper-evidence (independent, optional)
P8 LDAP decision · P9 ingestion hardening + OCR · P10 chat attachments (needs P9) · P11 OTel · P12 S3a (needs engine decision)
```

## Recommended first feature: Phase 3 (H1). Phase 1 is a small test/docs-only prerequisite.

H1 is verifiable on this machine (Ollama's own `/v1` and llama.cpp run on a Mac), changes no schema, is off-by-default, and is the prerequisite for H2, H2a and H4. The agentic path (the product's stated differentiator) cannot run on any non-Ollama backend until H1 then H2 land. If an NVIDIA host becomes available, H3 (Phase 6) can move to the front, since it is independent.

---

## Phase 1: Isolation and default-posture hardening (S)

- **Scope:** (a) a route-walk regression test asserting every retrieval-capable route obtains and applies a `RetrievalScope`, not only a role (`test_route_policy.py` covers roles only); (b) deployment docs state plainly that `AUTH__MODE=disabled` is open and show the minimum secure configuration; (c) document that ingestion jobs are admin-only and not caller-scoped.
- **Files:** `backend/tests/unit/test_route_scope_policy.py` (new), `README.md`, `.env.example`. No application code.
- **API/data:** none. **Security:** guards against a future route that bypasses scope. **Rollback:** revert.
- **Test plan:** new test fails if a route that calls OpenSearch search has no scope. **Acceptance:** test present and green; docs merged.

## Phase 2: H5, embedding dimension from config (S)

- **Problem:** `index_config_hybrid.py:41` hardcodes `1024`. `EMBEDDING__DIMENSION` and `OPENSEARCH__VECTOR_DIMENSION` do not drive the mapping.
- **Design:** `build_chunk_mapping(dimension)`; keep `DOCUMENT_CHUNKS_MAPPING = build_chunk_mapping(1024)` so existing imports and `TestIndexPayloadMatchesMapping` keep working. `EMBEDDING__DIMENSION` is the single source of truth; `OPENSEARCH__VECTOR_DIMENSION` is deprecated and warns if it differs. On startup, if an existing index's mapped dimension differs from config, log an error and make **ingestion** return 409 ("index dimension 1024 ≠ configured 768; reindex required") while search and the API stay up.
- **Files:** `services/opensearch/{index_config_hybrid,client}.py`, `config.py`, `routers/documents.py`, `tests/unit/...`, `.env.example`. **Data:** none (a dimension change on existing data is an explicit reindex, not silent bulk failures).
- **Test plan:** builder with 768 → `dimension: 768`; mocked existing 1024 index + config 768 → guard set, upload 409, search works; matching → no guard. **Acceptance:** default behaviour identical; mismatch is a clear 409. **Rollback:** revert.

## Phase 3: H1, OpenAI-compatible backend verification, and H4 docs (M) — **IMPLEMENTED, awaiting review** (vLLM/TGI live runs BLOCKED)

> **Outcome (2026-11-11).** Provider fixes, contract tests, `python -m src.cli.check_llm_backend`, `compose.llamacpp.yml`, `compose.vllm.yml` (untested template), `docs/deployment/inference-backends.md`, `.env.example` and README updated. Live-verified: Ollama `/v1` and llama.cpp (each also end to end through the API). Offline suite: 554 passed (529 baseline + 25 new), existing tests unmodified. **Remaining:** run `check_llm_backend` plus the end-to-end pass against vLLM and TGI on an NVIDIA host and update the matrix.

- **Scope:** make `OpenAICompatibleLLMProvider` trustworthy, then verify it. No new provider classes.
- **Provider fixes (OpenAI-compatible path only; Ollama default untouched):** send the system prompt as a `system` message; send `max_tokens` (`LLM__MAX_TOKENS`, default 1024) to avoid tiny server defaults; a 404 on `GET /models` reports "reachable (no /models)" rather than unhealthy; surface streaming SSE `error` payloads as exceptions.
- **Verification matrix:**

  | Backend | Where | How |
  |---|---|---|
  | Ollama `/v1` | this Mac | live |
  | llama.cpp `llama-server` (GGUF) | this Mac, Docker | live |
  | vLLM | NVIDIA host | contract tests from recorded shapes + manual checklist. **BLOCKED** here |
  | TGI | NVIDIA host | same. **BLOCKED** here |

- **Deliverables:** `docs/deployment/inference-backends.md` (per backend: image, launch flags, `LLM__*` values, known quirks); opt-in `compose.vllm.yml` and `compose.llamacpp.yml`; `.env.example` snippets; **H4:** GGUF via Ollama (library tags, Modelfile import) and AWQ/GPTQ/FP8 via vLLM, each labelled verified or unverified. Do not claim a format that has not been run.
- **Files:** `services/providers/llm/openai_compatible_provider.py`, the RAG prompt builder, `tests/unit/services/providers/test_providers.py` (extend), new docs and compose overlays. **Data:** none.
- **Test plan:** `httpx.MockTransport` contract tests (non-stream, stream, usage parsing, system message present, `max_tokens` sent, `/models` 404, SSE error); live: playground and `/v1/chat/completions` with `LLM__PROVIDER=openai-compatible` pointed at Ollama `/v1` and llama.cpp. Until Phase 4, `/ask`, `/stream`, `/ask-agentic` still use Ollama. That is expected and documented.
- **Acceptance:** supported-backend list in the docs matches what was actually run; the vLLM/TGI rows stay **BLOCKED** until run on a GPU host. **Rollback:** `LLM__PROVIDER=ollama`; revert.

## Phase 4: H2, route every LLM path through `LLMProvider` (M) — **IMPLEMENTED, awaiting review** (vLLM/TGI live runs BLOCKED)

> **Outcome (2026-11-11).** Delivered as designed, with these differences from the plan below: (1) the structured-output default is **`json_schema`**, not `json_mode`: live probes on Ollama `/v1` and llama.cpp gave `json_schema` 15/15, `json_mode` failed every query rewrite (it does not send the schema), `function_calling` errored or was unreliable; (2) `max_tokens` is sent through `extra_body` because `langchain-openai` sends `max_completion_tokens`, which older servers ignore; (3) the default model for `/ask`, `/stream`, `/ask-agentic` comes from `AskRequest.model`'s settings-derived default (`Settings.default_llm_model`), which left the routers, cache key and audit record untouched; (4) a pre-existing bug was fixed in the same change: `/api/v1/system/health` reported any provider returning a status dict as healthy; (5) `/api/v1/health` marks Ollama as degrading the service only when the LLM or embeddings use it. Offline suite: 581 passed (529 baseline + 52 new across Phases 3–4), existing tests unmodified apart from one shared-mock fixture in `tests/api/conftest.py`. Live: Ollama `/v1` and llama.cpp each ran ingest, `/ask`, `/stream`, `/ask-agentic` and `/v1/chat/completions`. **Caveat:** with a 1B model the agent's grader behaved differently under llama.cpp than under Ollama (see register §3.2). **Remaining:** vLLM/TGI runs on an NVIDIA host.

- **Depends on:** Phase 3.
- **Design:** add non-abstract `get_langchain_model(model=None, temperature=0.0, **kw)` to `LLMProvider` (`OllamaClient` already has it). The OpenAI-compatible provider returns `langchain_openai.ChatOpenAI(base_url=…)` (new dependency `langchain-openai`; refuse an empty endpoint; test that no tokenizer or other egress occurs). Structured output method via `LLM__STRUCTURED_OUTPUT_METHOD=json_mode|json_schema|function_calling` (default `json_mode`, the most broadly supported). Switch `/ask` and `/stream` from `OllamaDep` to `LLMProviderDep`. `Context` gains optional `llm_provider` and a `chat_model_source` that falls back to `ollama_client` so existing node fixtures keep working; the four nodes call `chat_model_source`. `/api/v1/health` gains an `llm` entry.
- **Files:** `services/providers/llm/*`, `routers/ask.py`, `dependencies.py`, `services/agents/{context,factory,agentic_rag}.py`, `agents/nodes/*`, `routers/ping.py`, `tests/unit/test_wiring_regressions.py` (add a test that pins nodes using `chat_model_source`; update signature pins only for intended changes), `tests/api/conftest.py`.
- **Test plan:** each node with `ollama_client`-only context (existing fixtures) and with an `llm_provider` mock; `/ask` calls the provider mock; no-egress test; live agentic run against Ollama `/v1`. **Acceptance:** with `LLM__PROVIDER=ollama` behaviour is unchanged; with an OpenAI-compatible provider all of `/ask`, `/stream`, `/ask-agentic` use it. **Rollback:** `LLM__PROVIDER=ollama`; revert.

## Phase 5: H2a, honour per-application provider/endpoint (M)

- **Depends on:** Phase 4. Approved-subset only (no persona system).
- **Design:** `ProviderRegistry` (app-state service via `make_provider_registry()`, following the CLAUDE.md pattern: factory, lifespan, dependency, patch in `tests/api/conftest.py`) returns a cached provider per `(type, endpoint, key-fingerprint)`. `answer.py` uses it when the application's `model_configuration.llm_endpoint` is set, otherwise the global provider (today's behaviour). Secret references: `api_key_ref` of the form `env:VAR_NAME` resolves from the environment (other values are used literally for compatibility). Optional `LLM__ALLOWED_ENDPOINT_HOSTS` (empty = any host, as today) restricts per-application endpoints and the provider `/test` and `/models` calls. Also decide whether `rag_strategy="agentic"` should run on the application path (currently stored but ignored).
- **Data:** none. **Security:** closes the SSRF-class surface when the allowlist is set. **Test plan:** SQLite domain test with a distinct mock provider per application; null endpoint → global; registry caching; `env:` resolution incl. missing variable → clear 500 naming the variable; allowlist rejects non-listed host. **Rollback:** clear `llm_endpoint` on affected applications; revert.

## Phase 6: H3, NVIDIA GPU overlay and residency probe (M; runtime proof BLOCKED here)

- **Design:** opt-in `compose.gpu.nvidia.yml` reserving GPUs on the `ollama` service (`deploy.resources.reservations.devices`, driver `nvidia`, count `all`, `capabilities: [gpu]`; needs the NVIDIA Container Toolkit); `make start-gpu`; docs: Docker on macOS cannot reach the Apple GPU, so Mac users run native Ollama and point `OLLAMA_HOST`, `LLM__ENDPOINT`, `EMBEDDING__ENDPOINT` at `host.docker.internal`. Fix `GET /api/v1/system/gpu`: keep the `nvidia-smi` probe but label it `source: "api-container"`, and add an Ollama probe, `GET {ollama}/api/ps`, reporting each loaded model's `size`, `size_vram` and a computed GPU percentage (`gpu`/`cpu`/`split`), so the endpoint reports whether inference is **actually** on GPU. Additive schema fields (`source`, `inference[]`); the System page renders a "Model residency" table.
- **Files:** `compose.gpu.nvidia.yml`, `Makefile`, `routers/system.py`, `schemas/api/*` (GPU response), `frontend/src/app/system/page.tsx`, `frontend/src/lib/api/types.ts`.
- **Test plan:** unit-test the probe with mocked `/api/ps` (full GPU, CPU-only, split, Ollama down → empty list, never an error); `docker compose -f compose.yml -f compose.gpu.nvidia.yml config` validates; `npm run build`. **Acceptance:** the config validates here; "inference on GPU" is **BLOCKED** until run on an NVIDIA host, and must not be claimed before then. **Rollback:** start without the overlay.

## Phase 7: API-key expiry and optional audit tamper-evidence (S + M, each optional)

- **API-key expiry (S):** a new nullable `expires_at` would be a new column on an existing table, which `create_all` will not add (ADR-0003). Use a new `application_api_key_expiry` side table, or get an explicit decision to introduce Alembic. Enforce in `auth/completions.py` and expose in `routers/api_keys.py` and the UI. Scopes and rate limits only if a buyer asks.
- **Audit tamper-evidence (M):** per-row hash chain (`prev_hash`, `row_hash`) written in the recorder, an `audit verify` CLI, and signed export manifests. Optional DB role without `UPDATE`/`DELETE`. Decide based on the buyer's compliance bar.
- **Test plan:** expiry boundary, expired-key 401, rotation; chain verification detects an altered row. **Rollback:** disable; do not drop `audit_events` on rollback.

## Phase 8: Identity decisions (S)

- **LDAP: BLOCKED / DECISION REQUIRED** (ADR-0001). Default recommendation: do not implement; federate through the buyer's IdP. If approved, scope it as an authenticator that maps LDAP groups to the existing claim-based grants, not a new identity store.
- **Verify against a real IdP** (JWKS fetch, key rotation, the admin UI OIDC browser flow): currently BLOCKED for lack of an IdP.

## Phase 9: Ingestion hardening and conditional OCR fallback (S + M)

- Ingestion hardening: upload size cap, content-type allowlist, sanitise the stored filename (store as `<checksum>` only), and a cleanup policy for `rag-uploads`.
- OCR: add scanned-PDF and image-heavy fixtures and measure Docling with `do_ocr` on/off. **Add a fallback engine only if a real gap is demonstrated.** Decide the VLM's role (the provider is built at startup but not used by ingestion).

## Phase 10: Chat attachments (L), after Phase 9

- Needs product decisions: supported types, whether images go to a VLM, retention and cleanup, and whether attachments are KB-ephemeral or never stored. `ChatMessage.content` is currently `str`, so the `/v1` schema would need OpenAI-style content parts. All attachment processing must be caller-scoped, size-limited and audited. Do not start before the temp-file lifecycle is designed.

## Phase 11: OpenTelemetry export (S–M, conditional)

- Only on a concrete interoperability requirement. Prefer one instrumentation layer over duplicating Langfuse. **Acceptance** is defined by that requirement.

## Phase 12: S3a, document-level ACLs (L, deferred)

- Side table `document_acl_tags`, an additive `acl_tags` keyword in the chunk mapping, scope filter `kb ∈ permitted AND (acl_tags ∈ principal values OR untagged)`. **Precondition:** switch the kNN engine from nmslib to lucene/faiss with an alias-swap reindex (ADR-0004), as filter selectivity makes the 10× oversampling unreliable.

## Cross-cutting risks and mitigations

| Risk | Mitigation |
|---|---|
| Backend claims outrun evidence | Support matrix lists only what was run; vLLM/TGI/GPU stay BLOCKED until run |
| Provider change breaks the Ollama default | Every change gated by `LLM__PROVIDER`; fallback in `Context`; existing fixtures unchanged |
| Reindex needed for dimension/engine change | Explicit, documented, guarded (409), never silent |
| Open-by-default | Phase 1 docs; `/system/security` reports the true state |

## Approval needed next

1. Approve **Phase 3 (H1)** as the first build. Or, if an NVIDIA host is available, nominate **Phase 6 (H3)** first.
2. Answer the open decisions: LDAP (yes/no), API-key expiry (wanted?), the nmslib → lucene engine change, and whether Phase 1 (the test-only, docs-only hardening) should go first.
