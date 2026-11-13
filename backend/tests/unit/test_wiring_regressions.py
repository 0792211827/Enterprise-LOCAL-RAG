"""Regression tests for wiring defects that unit mocks previously hid.

Each test here pins a real call contract between two collaborators. The bugs
these cover all passed the existing suite because the collaborator was replaced
by a ``MagicMock``, which happily accepts any signature or attribute.
"""

import sys
import threading
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from src.config import Settings
from src.dependencies import get_agentic_rag_service
from src.services.agents.factory import make_agentic_rag_service
from src.services.indexing.text_chunker import TextChunker
from src.services.langfuse.client import LangfuseTracer
from src.services.langfuse.tracer import RAGTracer
from src.services.ollama.client import OllamaClient
from src.services.providers.embeddings.huggingface_provider import HuggingFaceEmbeddingProvider


class TestAgenticServiceWiring:
    """`/api/v1/ask-agentic` builds its service through the DI provider."""

    def test_factory_accepts_model_and_applies_it(self):
        service = make_agentic_rag_service(
            opensearch_client=MagicMock(),
            ollama_client=MagicMock(),
            embeddings_client=MagicMock(),
            langfuse_tracer=None,
            model="llama3.2:1b",
        )
        assert service.graph_config.model == "llama3.2:1b"

    def test_factory_without_model_keeps_default(self):
        service = make_agentic_rag_service(
            opensearch_client=MagicMock(),
            ollama_client=MagicMock(),
            embeddings_client=MagicMock(),
        )
        assert service.graph_config.model

    def test_dependency_provider_constructs_service(self):
        """The provider passes ``model=``; the factory must accept it."""
        settings = Settings()
        service = get_agentic_rag_service(MagicMock(), MagicMock(), MagicMock(), MagicMock(), settings)
        assert service.graph_config.model == settings.ollama_model


class TestOllamaLangChainModel:
    """Every agent node calls ``ollama_client.get_langchain_model(...)``."""

    def test_client_exposes_get_langchain_model(self):
        assert hasattr(OllamaClient, "get_langchain_model")

    def test_returns_model_bound_to_client_config(self):
        client = OllamaClient(Settings())
        model = client.get_langchain_model(model="llama3.2:1b", temperature=0.0)

        assert model.model == "llama3.2:1b"
        assert model.base_url == client.base_url
        # Nodes rely on both of these.
        assert hasattr(model, "ainvoke")
        assert hasattr(model, "with_structured_output")

    def test_falls_back_to_default_model(self):
        client = OllamaClient(Settings())
        assert client.get_langchain_model().model == client.default_model


class TestShortDocumentChunking:
    """Documents below ``min_chunk_size`` words still produce one chunk."""

    @pytest.fixture
    def chunker(self):
        return TextChunker(chunk_size=600, overlap_size=100, min_chunk_size=100)

    def test_below_minimum_returns_single_chunk(self, chunker):
        text = "RAG grounds model answers in retrieved documents."
        chunks = chunker.chunk_text(text, "doc-1")

        assert len(chunks) == 1
        assert chunks[0].text == text
        assert chunks[0].metadata.word_count == len(text.split())

    def test_chunk_document_handles_short_full_text(self, chunker):
        chunks = chunker.chunk_document(title="T", abstract="A", full_text="only a few words", document_id="d")
        assert len(chunks) == 1

    def test_empty_text_returns_no_chunks(self, chunker):
        assert chunker.chunk_text("   ", "doc-1") == []


class TestLangfuseTracerContract:
    """`RAGTracer` and the agent nodes call these methods on ``LangfuseTracer``."""

    @pytest.fixture
    def tracer(self):
        # No credentials -> client is None -> every method takes its no-op path.
        return LangfuseTracer(Settings())

    @pytest.mark.parametrize("method", ["trace_rag_request", "create_span", "end_span", "update_span", "flush"])
    def test_required_methods_exist(self, tracer, method):
        assert hasattr(tracer, method)

    def test_create_and_end_span_are_safe_when_disabled(self, tracer):
        span = tracer.create_span(name="retrieval", trace=None, input_data={"q": "x"}, metadata={})
        assert span is None
        tracer.end_span(span, output={"status": "ok"})  # must not raise

    def test_rag_tracer_full_request_path(self, tracer):
        """Exercises the exact sequence `/api/v1/ask` runs per request."""
        rag_tracer = RAGTracer(tracer)

        with rag_tracer.trace_request("api_user", "what is rag?") as trace:
            with rag_tracer.trace_embedding(trace, "what is rag?"):
                pass
            with rag_tracer.trace_search(trace, "what is rag?", 3) as span:
                rag_tracer.end_search(span, [], [], 0)
            with rag_tracer.trace_prompt_construction(trace, []) as span:
                rag_tracer.end_prompt(span, "prompt")
            with rag_tracer.trace_generation(trace, "llama3.2:1b", "prompt") as span:
                rag_tracer.end_generation(span, "answer", "llama3.2:1b")
            rag_tracer.end_request(trace, "answer", 1.0)


class TestEmbeddingModelLoadIsSerialised:
    """``embed_*`` dispatches through ``asyncio.to_thread``, so loads can race.

    Each racing thread would otherwise build its own SentenceTransformer; at
    ~2.3GB for bge-m3 a handful of concurrent ingestions exhausts container
    memory.
    """

    def test_concurrent_threads_build_model_once(self, monkeypatch):
        provider = HuggingFaceEmbeddingProvider(model_name="fake-model", dimension=1024)
        constructions = []

        def slow_ctor(name, device=None):
            constructions.append(name)
            time.sleep(0.05)  # widen the race window
            model = MagicMock()
            model.get_sentence_embedding_dimension.return_value = 1024
            return model

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = slow_ctor
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

        threads = [threading.Thread(target=provider._ensure_model) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(constructions) == 1
        assert provider._model is not None


class TestIndexPayloadMatchesMapping:
    """The chunk index mapping is ``dynamic: strict``.

    Any field ``IngestionService`` writes that the mapping does not declare makes
    OpenSearch reject the whole bulk request with
    ``strict_dynamic_mapping_exception``, failing every ingestion. Unit tests
    mock ``bulk_index_chunks``, so only this contract check catches it.
    """

    def test_every_written_field_is_mapped(self):
        from types import SimpleNamespace

        from src.services.ingestion.service import IngestionService
        from src.services.opensearch.index_config_hybrid import DOCUMENT_CHUNKS_MAPPING

        mapping = DOCUMENT_CHUNKS_MAPPING["mappings"]
        assert mapping["dynamic"] == "strict", "test only meaningful for a strict mapping"
        mapped = set(mapping["properties"])

        service = IngestionService(chunker=MagicMock(), embeddings_provider=MagicMock(), opensearch_client=MagicMock())
        document = SimpleNamespace(id="doc-1", knowledge_base_id="kb-1", title="Doc")
        knowledge_base = SimpleNamespace(embedding_model="BAAI/bge-m3")
        chunk = SimpleNamespace(
            text="chunk text",
            metadata=SimpleNamespace(chunk_index=0, word_count=2, start_char=0, end_char=10, section_title=None),
        )

        payload = service._build_index_payload(document, knowledge_base, [chunk], [[0.0] * 1024])
        written = set(payload[0]["chunk_data"]) | {"embedding"}

        assert not (written - mapped), f"unmapped fields would be rejected: {sorted(written - mapped)}"


class TestTraceCarriesRequestId:
    """Langfuse traces carry the X-Request-ID so they join to audit events."""

    def _metadata(self, request_id):
        from unittest.mock import MagicMock

        from src.services.langfuse.tracer import RAGTracer

        tracer = MagicMock()
        with RAGTracer(tracer).trace_request("alice", "q", request_id):
            pass
        return tracer.trace_rag_request.call_args.kwargs["metadata"]

    def test_request_id_is_attached(self):
        assert self._metadata("rid-1") == {"simplified_tracing": True, "request_id": "rid-1"}

    def test_metadata_unchanged_without_one(self):
        assert self._metadata(None) == {"simplified_tracing": True}


class TestProviderRegistryWiring:
    """The registry's real builder (the API tests inject fakes) must construct real providers."""

    @pytest.fixture
    def registry(self):
        from src.services.providers.llm.factory import make_provider_registry

        return make_provider_registry(Settings())

    def test_openai_compatible_endpoint_gets_its_key(self, registry):
        from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

        provider = registry.for_endpoint("openai-compatible", "http://gpu:8000/v1/", "sk-1", "qwen")
        assert isinstance(provider, OpenAICompatibleLLMProvider)
        assert provider.endpoint == "http://gpu:8000/v1"
        assert provider._headers["Authorization"] == "Bearer sk-1"

    def test_ollama_endpoint_is_the_clients_host(self, registry):
        provider = registry.for_endpoint("ollama", "http://gpu:11434", None, "llama3.2:1b")
        assert isinstance(provider, OllamaClient)
        assert provider.base_url == "http://gpu:11434"

    def test_providers_are_cached_per_type_address_and_key(self, registry):
        first = registry.for_endpoint("openai-compatible", "http://gpu:8000/v1", "k1")
        assert registry.for_endpoint("OpenAI-Compatible", "http://gpu:8000/v1/", "k1") is first
        assert registry.for_endpoint("openai-compatible", "http://gpu:8000/v1", "k2") is not first
        assert registry.for_endpoint("ollama", "http://gpu:8000/v1", "k1") is not first

    def test_an_unknown_provider_type_is_refused_clearly(self, registry):
        from src.services.providers.llm.registry import ProviderConfigurationError

        with pytest.raises(ProviderConfigurationError, match="huggingface"):
            registry.for_endpoint("huggingface", "http://gpu", None)

    def test_an_application_without_an_endpoint_uses_the_default(self, registry):
        default = object()
        application = SimpleNamespace(model_configuration=SimpleNamespace(llm_endpoint=None))
        assert registry.for_application(application, default=default) is default
        assert registry.for_application(SimpleNamespace(model_configuration=None), default=default) is default


class TestSingleRetrievalSeam:
    """Every retrieval path searches through ``services.retrieval.retrieve``.

    That function owns scope intersection and filtering, so rerank and Document
    restrictions can be added once. A caller that searched directly would skip them.
    """

    SEARCH_METHODS = ("search_unified", "search_chunks_vector", "search_chunks_hybrid", "search_papers")
    ALLOWED = {"services/retrieval.py", "services/opensearch/client.py"}

    def test_no_caller_searches_the_index_directly(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "src"
        offenders = []
        for path in root.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in self.ALLOWED:
                continue
            text = path.read_text()
            offenders += [f"{relative}: .{m}(" for m in self.SEARCH_METHODS if f".{m}(" in text]
        assert offenders == [], f"search through services.retrieval.retrieve instead: {offenders}"

    def test_retrieve_signature_is_pinned(self):
        import inspect

        from src.services.retrieval import retrieve

        params = inspect.signature(retrieve).parameters
        assert list(params)[:3] == ["opensearch", "embeddings", "query"]
        assert all(params[name].kind is inspect.Parameter.KEYWORD_ONLY for name in list(params)[3:])
        assert {
            "scope", "requested_kb_ids", "mode", "size", "from_", "categories", "latest", "min_score",
            "query_embedding", "fall_back_to_bm25",
        } <= set(params)


class TestChatCollaborators:
    """Chat leans on collaborators whose real signatures a mock would hide."""

    def test_chat_tracing_is_a_no_op_on_a_real_tracer_without_credentials(self):
        from src.services.chat import trace_chat_exchange

        trace_chat_exchange(LangfuseTracer(Settings()), {"application_id": "a"}, question="q", answer="a")

    def test_chat_tracing_survives_a_langfuse_that_raises(self):
        from src.services.chat import trace_chat_exchange

        tracer = SimpleNamespace(client=SimpleNamespace(start_span=MagicMock(side_effect=RuntimeError("down"))), flush=MagicMock())
        trace_chat_exchange(tracer, {})  # tracing must never break an answer

    def test_both_real_providers_accept_conversation_history(self):
        import inspect

        from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

        for cls in (OpenAICompatibleLLMProvider, OllamaClient):
            assert "history" in inspect.signature(cls.generate_rag_answer_stream).parameters, cls

    def test_the_ollama_prompt_carries_the_conversation_so_far(self):
        from src.services.ollama.prompts import RAGPromptBuilder

        history = [{"role": "user", "content": "Refund window?"}, {"role": "assistant", "content": "14 days."}]
        prompt = RAGPromptBuilder().create_rag_prompt("and EU?", [], history=history)
        assert prompt.index("User: Refund window?") < prompt.index("Assistant: 14 days.") < prompt.index("and EU?")
        assert "Conversation so far" not in RAGPromptBuilder().create_rag_prompt("q", [])
