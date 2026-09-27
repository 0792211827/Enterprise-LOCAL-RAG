# Hardware and backend-abstraction reference notes (Phase 2b)

*Hardware/Backend track, researched separately from the security track.
Reference: Open WebUI (OWUI) at commit `8bd8b4f`, read-only, cloned to
`../reference/open-webui`. Paths prefixed `owui:` are relative to
`owui:backend/open_webui/`. Our paths are relative to `backend/`. No code was
copied. These notes describe patterns in our own words.*

## TL;DR

1. **OWUI has no inference-backend interface class.** Its abstraction is *two
   wire protocols*, Ollama-native and OpenAI-compatible HTTP, each with N
   configurable base URLs. Everything is normalized to OpenAI's
   chat-completions shape. We **already have a stronger in-process
   abstraction** (`LLMProvider` ABC with Ollama and OpenAI-compatible
   implementations). The gap is that **most of our code paths bypass it**.
2. **vLLM, TGI, llama.cpp-server, SGLang and LM Studio are "free" through an
   OpenAI-compatible connection, in OWUI and in our stack.** OWUI has **no**
   vLLM, TGI or TensorRT-specific code at all. The only per-backend code it
   has is model load/unload management for llama.cpp and LM Studio. Our
   `OpenAICompatibleLLMProvider` already targets `/chat/completions` and
   `/models`, and the factory even accepts `LLM__PROVIDER=vllm`. It needs to be
   verified against real servers, and then wired into the paths that currently
   hard-code Ollama.
3. **OWUI does no GPU detection for LLM inference.** It only picks a torch
   device (`cuda`/`mps`/`cpu`) for in-process embedding and reranker models.
   GPU enablement is a compose overlay that reserves GPUs *on the Ollama
   container*. **Our compose passes no GPU to anything.** On a Linux GPU host
   our Ollama runs on CPU today. Our existing `/system/gpu` endpoint runs
   `nvidia-smi` in the wrong container.
4. **Quantization is delegated to the backend in OWUI too.** The only explicit
   support is "import a GGUF file into Ollama". GGUF works through Ollama for
   us today. AWQ and GPTQ are vLLM/TGI formats and come "free" once the
   OpenAI-compatible path is verified.

---

## 1. Inference backend abstraction

### Pattern observed (OWUI)

- **Dispatcher, not interface.** `owui:utils/chat.py:148`
  `generate_chat_completion` looks up the model in an app-state registry and
  branches on its origin: in-process Python "pipe" plugin → Ollama →
  everything else (OpenAI-compatible).
- **Connections are lists.** Separate environment variables list the Ollama
  base URLs and the OpenAI-compatible base URLs, each with a parallel list of
  keys and per-connection JSON config (`owui:config.py:296-359`). That config
  holds an enable flag, a `prefix_id` that namespaces model IDs, a static
  `model_ids` allow-list, `connection_type` local/external, and a `provider`
  hint (`llama.cpp`, `lmstudio`, `azure`, `litellm`). It is persisted in the
  DB and editable from the admin UI.
- **One merged model catalogue.** `owui:utils/models.py:58-69` fetches model
  lists from every connection concurrently and merges them. Each model
  remembers which connection serves it (`urlIdx`), and requests route by model
  ID. Ollama models served by several nodes are picked **at random**, a
  primitive load balancer (`owui:routers/ollama.py:1093`). OpenAI-compatible
  duplicates take first-wins, with no balancing.
- **Normalization at the edge.** Ollama's NDJSON stream is converted to
  OpenAI SSE (`owui:utils/response.py:230-270`). OpenAI-compatible streams are
  passed through untouched. Usage and token counting is normalized across
  OpenAI, Ollama and llama.cpp formats (`owui:utils/response.py:13`).
- **Embeddings** are a separate engine switch (`RAG_EMBEDDING_ENGINE`: local
  SentenceTransformers, Ollama, OpenAI-compatible or Azure) with its own base
  URLs (`owui:retrieval/utils.py:1115-1206`). **Vector stores** are the one
  place OWUI *does* use a real ABC: `VectorDBBase`
  (`owui:retrieval/vector/main.py:24-101`), with 13+ implementations picked by
  `VECTOR_DB`. Its OpenSearch implementation is weak by comparison with ours.
  It creates one index per collection and uses brute-force `script_score`
  cosine rather than ANN (`owui:retrieval/vector/dbs/opensearch.py:140-143`),
  and it has no hybrid BM25+kNN.

LangChain's pattern is similar to our ABC: `BaseChatModel` and `Embeddings`
interfaces with provider packages (`langchain_ollama`, `langchain_openai`). We
already depend on `langchain_ollama.ChatOllama` for the agentic graph. The
OpenAI-compatible equivalent would be `langchain_openai.ChatOpenAI` pointed at
a local `base_url`, which makes no external call if it is configured to a local
endpoint.

### Our current state

| Concern | State |
|---------|-------|
| LLM interface | `services/providers/llm/base.py`, an `LLMProvider` ABC with `health_check`, `list_models`, `generate`, `generate_stream`, `generate_rag_answer`, `generate_rag_answer_stream`. Implemented by `OllamaClient` and `OpenAICompatibleLLMProvider`. Chosen by `LLM__PROVIDER` in `providers/llm/factory.py`. |
| **Who actually uses it** | **Only** the application path (`routers/applications.py:157`, `routers/chat_completions.py:89`) and `routers/system.py:54`. |
| Who bypasses it | Legacy `/ask` and `/stream` use `OllamaDep` directly (`routers/ask.py:147,243`). All four agentic nodes call `ollama_client.get_langchain_model()`, which returns a `ChatOllama` (`agents/nodes/*_node.py`). `/api/v1/health` checks Ollama only (`routers/ping.py:59`). So with `LLM__PROVIDER=vllm`, the playground and `/v1` use vLLM while `/ask`, `/stream` and `/ask-agentic` still require a running Ollama. |
| Per-application backend | `ModelConfiguration` stores `llm_provider` and `llm_endpoint` per application (`models/provider.py:50-51`), and the admin UI collects them. **Runtime ignores both.** `answer.py:58` only reads `llm_model` and hands it to the single global provider. An application configured for a vLLM endpoint silently runs on whatever `LLM__PROVIDER` says. |
| Provider registry | The `model_providers` table plus `/api/v1/providers` CRUD, `/test` and `/models`. It is used by the admin UI for testing and listing, **but never consulted when answering**. |
| Multiple connections | One LLM endpoint, one embedding endpoint and one VLM endpoint per process. There is no model catalogue across endpoints. |
| Embeddings | `EmbeddingProvider` ABC with HuggingFace (local, `EMBEDDING__DEVICE` cuda/mps/cpu/auto), Ollama and OpenAI-compatible implementations (`tei` and `vllm` are aliases). **This is already config-driven**, which is good. |
| Vector store | OpenSearch only, and `OpenSearchClient` is concrete with no interface. That's acceptable given the product decision to run on OpenSearch hybrid search. |
| **Dimension drift hazard** | The index mapping hard-codes `"dimension": 1024` (`services/opensearch/index_config_hybrid.py`). `OPENSEARCH__VECTOR_DIMENSION` exists (`config.py:135`) but nothing reads it, and `EMBEDDING__DIMENSION` and `knowledge_bases.embedding_dimension` are separate again. Switching to a non-1024-dimensional embedding model through config makes every bulk index request fail. "Swap the embedding provider by config" therefore only works for 1024-dimensional models today. |

### What adopting an equivalent pattern would take

1. **Route every LLM path through `LLMProvider`.** Switch `/ask` and `/stream`
   from `OllamaDep` to `LLMProviderDep`; `OllamaClient` already implements the
   same methods. For the agentic graph, add a `get_langchain_model()` method to
   the provider interface. Ollama returns `ChatOllama`, and OpenAI-compatible
   returns `ChatOpenAI(base_url=endpoint)`. Nodes then call
   `context.llm_provider.get_langchain_model()`. Behaviour is identical when
   `LLM__PROVIDER=ollama`, the default. This is a **moderate refactor** and
   non-breaking. `tests/unit/test_wiring_regressions.py` and the agent-node
   fixtures need matching updates.
2. **Health check follows the configured provider** rather than always Ollama.
   **Additive.**
3. **Honour per-application provider and endpoint.** Resolve a provider per
   request from `ModelConfiguration` (or from a `model_providers` row), with a
   small cache keyed by `(type, endpoint)`. Fall back to the global provider
   when unset, which covers every existing row because the defaults are
   `ollama` with a null endpoint. **Moderate.**
4. **Build the mapping from config.** Take the dimension from settings, and
   refuse to start (or refuse ingestion) when the existing index dimension
   doesn't match the configured embedding dimension. **Additive** for new
   indices. Changing the dimension on an existing index is inherently a
   reindex.
5. **Multi-connection model catalogue**, as in OWUI's merged list. **Defer.**
   Buyers typically standardize on one inference server per deployment, and
   per-application endpoints (item 3) cover the real need.

---

## 2. Backends beyond Ollama

| Backend | Wire API | Works through our existing OpenAI-compatible provider? | Notes |
|---------|----------|---------------------------------------------------------|-------|
| **vLLM** | OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/v1/embeddings` | **Expected yes, unverified.** The factory already accepts `vllm`. Set `LLM__ENDPOINT=http://vllm:8000/v1`. | Multi-GPU is a *server flag* (`--tensor-parallel-size N`), not our concern. It serves AWQ, GPTQ and FP8 checkpoints natively. The API key is optional (`--api-key`). |
| **TGI** (text-generation-inference) | Has an OpenAI-compatible Messages API at `/v1/chat/completions` (TGI ≥ 1.4) | **Expected yes, unverified.** `/v1/models` support varies by version, and our `health_check` and `list_models` call it. | TGI is in maintenance mode upstream (HF now points users to vLLM/SGLang). Document it; don't invest. |
| **TEI** (text-embeddings-inference) | OpenAI-compatible `/v1/embeddings` | Embeddings factory already accepts `tei` | Good fit for GPU embeddings off the API container. |
| **llama.cpp** (`llama-server`) | OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/v1/embeddings` | **Expected yes, unverified.** | A CPU and low-VRAM story using GGUF directly. `llama-cpp-python` in-process would be a *new* adapter. Not recommended: it puts a native build and model memory inside the API process. |
| **SGLang, LocalAI, LM Studio, NVIDIA NIM** | OpenAI-compatible | Expected yes | NIM is TensorRT-LLM under the hood with an OpenAI API. |
| **TensorRT-LLM** | Native: Triton gRPC/HTTP or `trtllm-serve`. Recent releases of `trtllm-serve` expose an OpenAI-compatible API. | **Probably yes via `trtllm-serve` or NIM.** Raw Triton would need a new adapter. | High setup cost (engine builds per GPU architecture). Scope it only if a buyer mandates raw Triton. |

**Known risks in our OpenAI-compatible provider to verify** (code-read,
`providers/llm/openai_compatible_provider.py`):
- System prompts are folded into a single `user` message (`_messages`, l.39)
  rather than sent as a `system` role. That works, but it is suboptimal for
  chat-tuned models, and some servers apply chat templates differently.
- `health_check` depends on `GET {endpoint}/models`. Servers without it would
  report unhealthy while still generating fine.
- The streaming parser skips malformed lines silently. That's fine, but a
  server that sends errors as SSE `data:` lines would yield an empty answer
  rather than an error.
- There is no `max_tokens` default. Some servers default to very small limits
  (TGI's historic default was 20 tokens), so answers could be truncated.
- The agentic graph can't use this provider at all until item 1.1 above is
  done.

**How invasive:** verification plus documentation is **additive** (a compose
overlay example, `.env.example` snippets, and a README section). Making the
remaining paths use it is the **moderate refactor** in section 1.

---

## 3. GPU detection and configuration

### Pattern observed (OWUI)

- **No hardware inventory.** Nothing in OWUI's Python backend calls
  `nvidia-smi` or NVML, reads `CUDA_VISIBLE_DEVICES`, handles ROCm or selects
  among multiple GPUs. `owui:env.py:47-73` only picks a torch device for
  in-process models: CUDA when `USE_CUDA_DOCKER=true` and
  `torch.cuda.is_available()`, MPS on macOS (with a lock, because MPS isn't
  thread-safe), otherwise CPU.
- **GPU enablement is deployment config:**
  - A `docker-compose.gpu.yaml` overlay reserves `${OLLAMA_GPU_COUNT-1}` GPUs
    with driver `${OLLAMA_GPU_DRIVER-nvidia}` **on the Ollama service**.
  - An AMD overlay passes through `/dev/kfd` and `/dev/dri` and uses Ollama's
    `rocm` image tag.
  - A launcher script sniffs `nvidia-smi` or `lspci` to decide which overlay
    to layer on.
  - Separate `:cuda` image tags carry CUDA torch; the default image uses CPU
    torch.
- **No hardware in the UI.** The only runtime signal is a per-model "loaded"
  flag (Ollama `/api/ps`, llama.cpp and LM Studio status) plus unload buttons.

### Our current state

- **`compose.yml` gives no service a GPU.** The `ollama` service has no
  `deploy.resources.reservations.devices`. On a Linux + NVIDIA host, **Ollama
  runs CPU-only today** unless the operator edits compose. On macOS no Docker
  container can reach the Apple GPU, so native Ollama is the only
  GPU-accelerated option there.
- **`GET /api/v1/system/gpu` already exists** (`routers/system.py:170`) and the
  admin System page polls it every 10 s (`frontend/src/app/system/page.tsx:21`).
  It is honest (it never fabricates values) but **structurally wrong**. It runs
  `nvidia-smi` *inside the API container*, which is built from
  `python:3.12.8-slim`, has no NVIDIA runtime and no GPU reservation. It will
  report "unavailable" on every deployment, including GPU hosts. The GPU that
  matters is the inference server's.
- **The API image is heavy.** It ships CUDA 12 torch wheels (`torch 2.8.0`
  from PyPI plus `nvidia-*-cu12` packages in `uv.lock`), but no GPU is passed
  to it. HuggingFace embeddings therefore run on CPU in the container.
  `EMBEDDING__DEVICE=cuda` would fail or fall back to CPU. That is several GB
  of unused CUDA libraries.

### A minimal "detect and surface hardware" feature

Additive, read-only, no risk to existing flows:

1. **GPU compose overlays** (`compose.gpu.nvidia.yml`, `compose.gpu.amd.yml`)
   that reserve GPUs on `ollama`, using the same variable-default pattern as
   OWUI (`OLLAMA_GPU_COUNT`, `OLLAMA_GPU_DRIVER`). They are opt-in, and
   `compose.yml` is untouched. Optionally add a `make start-gpu` target.
2. **Report hardware from where inference runs.**
   - For Ollama, `GET /api/ps` returns the loaded models with `size_vram`
     versus `size`, which directly answers "is this model on the GPU, and how
     much of it". Surface that as "model X: 100% GPU, 4.1 GB VRAM". It needs no
     privileges and no nvidia-smi.
   - For vLLM, its `/metrics` Prometheus endpoint exposes GPU cache usage.
     vLLM also logs the GPU count, but it has no stable JSON hardware API.
   - Keep the existing nvidia-smi probe as a fallback for when the API
     container *is* given a GPU, and rename its message to say what it
     actually measured ("no GPU visible to the API container").
3. **Extend `GPUResponse`** with `source` (`api-container` / `ollama` /
   `vllm`) and a per-model VRAM residency list. This is an additive schema
   change, and the frontend only gets new optional fields.
4. **Out of scope for the minimal version:** multi-node inventory,
   tensor-parallel configuration from our UI (a server launch flag, not a
   runtime setting), and NVML in-process.

---

## 4. Quantized model support

| Format | Via Ollama today | Via other backends |
|--------|------------------|--------------------|
| **GGUF** (Q4_K_M, Q8_0, …) | **Yes.** Ollama's library models *are* GGUF quantizations (e.g. `llama3.2:1b` is Q8_0 by default, with other tags for other quants). Custom GGUF files import with a Modelfile `FROM ./model.gguf` plus `ollama create`, run by the operator in the container. | llama.cpp server natively. vLLM has experimental GGUF support. |
| **Safetensors FP16/BF16** | Importable. `ollama create --quantize q4_K_M` can quantize at import for supported architectures. | vLLM, TGI and SGLang natively. |
| **AWQ / GPTQ** | **No.** Ollama doesn't load them. | **vLLM, TGI and SGLang natively.** This is the main reason a regulated buyer with NVIDIA hardware would pick vLLM. |
| **FP8 / TensorRT engines** | No | vLLM (FP8 on Hopper/Ada), TensorRT-LLM or NIM |

OWUI handles this identically. Quantization is "whatever the backend
supports", plus an admin-only Ollama helper that downloads or uploads a GGUF
file and registers it via Ollama's blob and create API
(`owui:routers/ollama.py:1604-1720`), and a UI that displays the quantization
level from model metadata.

**What it would take in our stack:**
- Nothing, for GGUF through Ollama. Document the `ollama pull <tag>` and
  Modelfile import workflow.
- AWQ and GPTQ come "free" once vLLM or TGI is verified through the
  OpenAI-compatible provider (section 2).
- **Optional, additive:** show `details.quantization_level` and
  `parameter_size`, which Ollama's `/api/tags` already returns, in the
  providers/models UI. That's display only.
- **Not recommended:** a GGUF upload endpoint like OWUI's. It adds a large-file
  upload surface to the API, and the OWUI version fetches from
  HuggingFace/GitHub, which conflicts with our no-external-calls positioning
  (air-gapped buyers side-load models via the volume anyway).

---

## Invasiveness summary

| Item | Invasiveness |
|------|--------------|
| Document and verify vLLM, TGI and llama.cpp through the existing OpenAI-compatible provider (compose overlay example + env snippets) | **Additive** |
| GPU compose overlays for Ollama (NVIDIA / AMD) | **Additive** (opt-in files; `compose.yml` untouched) |
| Fix `/system/gpu` to report inference-server VRAM residency (Ollama `/api/ps`) | **Additive** (new optional response fields) |
| Build the index mapping dimension from config, with a mismatch guard | **Additive** for new indices. A dimension change on existing data is a reindex. |
| Route `/ask`, `/stream`, agentic nodes and health through `LLMProvider` | **Moderate refactor**, non-breaking under default config |
| Honour per-application `llm_provider` and `llm_endpoint` | **Moderate refactor**; existing rows keep today's behaviour |
| Multi-connection merged model catalogue (OWUI-style) | **Moderate to large**. Defer. |
| In-process llama-cpp-python or raw Triton adapters | **Large**. Only on explicit buyer demand. |
| Slim CPU-only API image (drop unused CUDA torch) | **Moderate** (build change, needs a separate `:cuda` variant if in-process GPU embeddings are ever wanted). Optional. |
