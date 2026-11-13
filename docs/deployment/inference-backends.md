# Inference backends

The platform generates answers through an `LLMProvider`. Ollama is the default. Any server that speaks the OpenAI `/v1/chat/completions` protocol can be used instead by setting `LLM__PROVIDER=openai-compatible` (aliases: `openai`, `vllm`, `nim`, `sglang`, `localai`).

This page lists **only what has actually been run**. A backend is not "supported" because it should work in theory. Evidence and dates are recorded below, and unrun rows stay marked as such.

## Support matrix

| Backend | Status | Evidence |
|---|---|---|
| Ollama (native API, the default `LLM__PROVIDER=ollama`) | **Verified** | Existing default; used by every path |
| Ollama via its OpenAI-compatible `/v1` | **Verified live** (2026-11-11) | `check_llm_backend` 8/8 checks. Full API run with `LLM__PROVIDER=openai-compatible`: ingest, `/ask`, `/stream`, `/ask-agentic` (LangGraph) and `/v1/chat/completions`, all answering from the documents. The agent's structured calls: 15/15 with `json_schema` |
| llama.cpp `llama-server`, GGUF, CPU | **Verified live** (2026-11-11) | Same full API run through `compose.llamacpp.yml`, with Ollama's chat model confirmed unused. The agent's structured calls: 15/15 with `json_schema`. See quirks and the small-model caveat below |
| vLLM | **Contract-tested only; live run BLOCKED** | Mocked responses in `tests/unit/services/providers/test_openai_compatible_contract.py`. Needs an NVIDIA GPU host. `compose.vllm.yml` is an **untested template** |
| TGI (Text Generation Inference) | **Contract-tested only; live run BLOCKED** | Same. No overlay provided |
| SGLang, LocalAI, NVIDIA NIM | **Not tested** | Accepted as `LLM__PROVIDER` aliases only |

"Contract-tested" means the provider handles the response shapes those servers document (role-only and null-content stream chunks, usage-only trailing chunks, error payloads in the stream, a missing `/models`). It does not show that the server itself starts or serves your model correctly. To move a row to **Verified**, run the check below against it and record the result here.

## What is routed through the configured backend

Every answer path uses `LLM__PROVIDER`, including the agentic graph:

| Path | Uses `LLM__PROVIDER`? |
|---|---|
| `POST /v1/chat/completions` (application path) | Yes |
| `POST /api/v1/applications/{id}/ask` (admin playground) | Yes |
| `POST /api/v1/ask`, `POST /api/v1/stream` | Yes |
| `POST /api/v1/ask-agentic` (LangGraph: guardrail, grading, query rewrite, answer) | Yes |
| `GET /api/v1/health` (`llm` entry), `GET /api/v1/system/health` (Inference) | Yes |
| Provider "test" button for an Ollama-type provider | Ollama |
| Per-application provider/endpoint fields | **Ignored.** Only the application's *model name* is used (planned: roadmap Phase 5) |

Ollama is still needed when **embeddings** use it (`EMBEDDING__PROVIDER=ollama`), for an Ollama-type provider row, or for an Ollama VLM. `GET /api/v1/health` reports it either way, but marks the service degraded for a missing Ollama only when the LLM or the embeddings are configured to use it.

**Which model is used.** A request to `/ask`, `/stream` or `/ask-agentic` that names no `model` gets the active provider's default: `OLLAMA_MODEL` for Ollama, `LLM__MODEL` for any other provider. The application path uses the application's configured model (`llm_model`). Whichever applies, it must be a name the backend serves, for example vLLM's `--served-model-name` or `LLAMACPP_MODEL_ALIAS` below.

**The agentic graph on an OpenAI-compatible backend.** The guardrail, grading and rewrite steps ask the server for JSON matching a schema. `LLM__STRUCTURED_OUTPUT_METHOD` chooses how:

| Method | Result on Ollama `/v1` and llama.cpp (15 calls each, the agent's real prompts) |
|---|---|
| `json_schema` (default) | 15/15 on both. The server enforces the schema |
| `json_mode` | Works for guardrail and grading, but **fails every query rewrite**: the schema is not sent, so the model picks its own keys |
| `function_calling` | Errors on llama.cpp (needs `--jinja` for tools); unreliable on Ollama |

Keep `json_schema` unless your server does not support `response_format` with a JSON schema. vLLM and TGI have not been run, so this default is **unverified there**.

**Model size matters.** The agent depends on the model judging relevance. With `llama3.2:1b`, Ollama (native or `/v1`) graded the test document relevant after one rewrite, while llama.cpp graded it not relevant in every run, so the agent gave up after two attempts and answered that it found nothing. Plain `/ask`, `/stream` and `/v1` answered correctly in all cases. The structured calls parsed fine; this is the small model behaving differently under llama.cpp's chat template, not a routing fault. Use a larger model for agentic retrieval and test it with your documents.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LLM__PROVIDER` | `ollama` | `ollama` or an OpenAI-compatible alias |
| `LLM__ENDPOINT` | `http://localhost:11434` | For OpenAI-compatible backends, the base URL **including `/v1`** |
| `LLM__MODEL` | `llama3.2:1b` | Default model name |
| `LLM__API_KEY` | empty | Bearer token, if the endpoint needs one |
| `LLM__TIMEOUT` | `300` | Seconds |
| `LLM__MAX_TOKENS` | `1024` | Sent as `max_tokens` on OpenAI-compatible endpoints (including the agentic graph). Some servers default to a tiny limit and silently truncate answers. Not used by Ollama's native API |
| `LLM__STRUCTURED_OUTPUT_METHOD` | `json_schema` | How the agent asks an OpenAI-compatible server for JSON: `json_schema`, `json_mode` or `function_calling`. See above. Not used by Ollama's native API |

## Checking a backend

Run the same code the API uses against the endpoint:

```bash
# on a host with the repo's Python env
cd backend && python -m src.cli.check_llm_backend http://HOST:PORT/v1 SERVED_MODEL_NAME

# or from inside the running stack
docker exec rag-api python -m src.cli.check_llm_backend http://vllm:8000/v1 SERVED_MODEL_NAME
```

It checks health, model listing, a plain completion, a grounded RAG answer, a streamed RAG answer, and whether an unknown model is rejected. `PASS`/`FAIL` are required. `INFO` lines (model listing, unknown-model rejection) are informational because some servers legitimately lack them. Exit status is non-zero if any required check fails.

Then do an end-to-end pass: create a knowledge base, upload a document, create an application whose model is the served name, and call `POST /v1/chat/completions` with `"stream": false` and `"stream": true`.

## Backend notes

### Ollama `/v1`

`LLM__PROVIDER=openai-compatible`, `LLM__ENDPOINT=http://ollama:11434/v1`. The model must already be pulled; an unknown model returns 404.

### llama.cpp `llama-server` (CPU, GGUF)

```bash
LLAMACPP_MODELS_DIR=/path/to/gguf-dir LLAMACPP_MODEL_FILE=model.gguf \
LLAMACPP_MODEL_ALIAS=my-model \
  docker compose -f compose.yml -f compose.llamacpp.yml up -d
```

The overlay adds the `llamacpp` service (no published port, health-checked on `/health`) and points the API at it. Set the RAG application's model to `LLAMACPP_MODEL_ALIAS`.

Quirks seen live:
- `llama-server` serves the single loaded model and **ignores the requested model name**, so a wrong name does not error. Rely on the application's model matching the alias for clarity, not for validation.
- Model load takes several seconds; the overlay waits for `/health`.
- **Memory.** Running llama-server next to Ollama (with a chat model resident), OpenSearch and the API on a memory-limited Docker Desktop got llama-server OOM-killed once mid-test; the API then logged "Connection error" and the agent fell back to its conservative defaults. If the backend container exits (check `docker inspect --format '{{.State.OOMKilled}}'`), raise Docker's memory limit or free memory (for example `ollama stop <chat-model>` when Ollama is only serving embeddings).

### vLLM (NVIDIA GPU) — untested template

```bash
VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct VLLM_SERVED_NAME=qwen \
  docker compose -f compose.yml -f compose.vllm.yml up -d
```

`compose.vllm.yml` reserves the GPU, runs `vllm serve`, and sets `LLM__PROVIDER=vllm`. It has **not** been run. It requires the NVIDIA Container Toolkit. Pin `VLLM_IMAGE` to a tested version. Flags for quantized models go in `VLLM_EXTRA_ARGS`. Air-gapped hosts should mount the model and point `VLLM_MODEL` at a local path.

### TGI — no overlay

Run TGI separately (its Messages API is at `/v1/chat/completions`) and set `LLM__PROVIDER=openai-compatible`, `LLM__ENDPOINT=http://<tgi>/v1`. TGI's default generation limit is small, which is why `LLM__MAX_TOKENS` is always sent. Not verified here.

## Quantized model formats

| Format | How it is served | Status |
|---|---|---|
| GGUF | Ollama (native), or llama.cpp `llama-server` | **Verified**: a GGUF model loaded and answered through both Ollama and `llama-server` (2026-11-11) |
| AWQ / GPTQ / FP8 | vLLM (`--quantization …`) or TGI (`--quantize …`), per each server's documentation | **Unverified**: depends on a vLLM/TGI run that has not been done. Do not rely on it until the matrix above shows a verified row |

The platform itself is format-agnostic: it sends chat requests and never loads weights. Format support is whatever the backend provides.

## Per-Application endpoints

`LLM__*` is the platform default. An Application can instead name its own chat Inference endpoint (for example a new GPU server) in its model configuration; every other Application keeps using the default. Set it on create or with `PATCH /api/v1/applications/{id}`:

| Field | Meaning |
|---|---|
| `llm_provider` | `ollama` or `openai-compatible` (aliases: `openai`, `vllm`, `nim`, `sglang`, `localai`) |
| `llm_endpoint` | Base URL, including `/v1` for OpenAI-compatible servers. Empty means the default provider |
| `llm_api_key_ref` | `env:NAME` reads the key from the API server's environment variable `NAME` at each request, so secrets never sit in the database and rotation needs no edit. Any other value is used literally (compatibility; prefer `env:`). A literal key is never returned by the API |
| `llm_model` | Model name sent to that endpoint |

The playground (`POST /api/v1/applications/{id}/ask`) and `POST /v1/chat/completions` both honour it. Providers are cached per endpoint type, address and key fingerprint. If a referenced variable is not set the request fails with a 500 naming the variable. Ollama endpoints take no key.

### The agentic strategy

An Application whose `rag_strategy` is `agentic` runs the agentic graph (guardrail, retrieve, grade, rewrite, generate) on the playground and on `/v1/chat/completions`, using the chat model of that Application's resolved provider. Retrieval inside the graph is limited to the Application's attached Knowledge Bases that the caller may read, and the audit record carries the same fields as a traditional answer plus `details.agentic`. A caller with no permitted Knowledge Base gets the usual refusal and the graph never runs. Differences to know: the graph produces its answer in one piece, so a streamed `/v1` response arrives as a single content chunk; the Application's system prompt is not applied inside the graph; and a 1B model gives noisy guardrail and grading decisions.

### Restricting endpoint hosts

`LLM__ALLOWED_ENDPOINT_HOSTS` (comma-separated, empty = any host) limits which hosts those endpoints may point at. A bare host allows any port; `host:port` allows only that port; matching is exact, never by suffix. A non-listed endpoint is rejected with `400` when an Application is created or updated, refused with a `500` naming the host if a saved one is used after the list changed, and the admin-only provider `test` and `models` actions answer `400` for a non-listed provider endpoint. Set it in production: those endpoints and actions make the API server call admin-supplied URLs.

Embeddings are not per-Application: retrieval always embeds with the platform's `EMBEDDING__*` provider.

## Reranking

An optional reranking Inference endpoint reorders retrieved passages before the answer is generated. It is **off by default** (`RERANK__ENABLED=false`); with it off, retrieval is exactly as before.

| Setting | Meaning |
|---|---|
| `RERANK__ENABLED` | Turn reranking on |
| `RERANK__ENDPOINT` | The full rerank URL, e.g. `http://reranker:8000/v1/rerank`. Required when enabled |
| `RERANK__MODEL`, `RERANK__API_KEY` | Sent with each request when set |
| `RERANK__CANDIDATES` | How many candidates to fetch and rerank (default 30; 20 to 50 is typical). At least the requested count is always fetched |
| `RERANK__TIMEOUT` | Seconds before giving up (default 5) |

The endpoint must speak the common rerank shape used by Cohere, Jina and vLLM: it receives `{"model", "query", "documents": [...]}` and answers `{"results": [{"index", "relevance_score"}]}`. Reranking happens after the hybrid search has fused and filtered results, so the endpoint only ever sees passages the caller is already permitted to read, and an answer that names an index it was not sent is rejected. The result is then cut to the requested count. It applies on every path: Applications (playground and `/v1`), `/ask`, `/stream`, `/hybrid-search` and the agentic retriever. Later pages of `/hybrid-search` (`from` above 0) are not reranked.

If the endpoint errors, times out or answers something unusable, the request still succeeds with the original order, a warning is logged, and the audit event carries `details.rerank_skipped: true` (`details.reranked: true` when it worked). Check with a Golden set before and after enabling it (see the eval harness docs) rather than assuming it helps.

## Rollback

Set `LLM__PROVIDER=ollama` (or start without the overlay file) and restart the API. Nothing persistent changes.
