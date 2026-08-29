"""Unit tests for the local-first model provider abstractions."""
import pytest
from src.config import EmbeddingSettings, LLMSettings, Settings, VLMSettings
from src.services.ollama.client import OllamaClient
from src.services.providers.embeddings.factory import make_embedding_provider
from src.services.providers.embeddings.huggingface_provider import HuggingFaceEmbeddingProvider
from src.services.providers.embeddings.ollama_provider import OllamaEmbeddingProvider
from src.services.providers.embeddings.openai_compatible_provider import (
    OpenAICompatibleEmbeddingProvider,
)
from src.services.providers.llm.base import LLMProvider
from src.services.providers.llm.citations import build_citations, build_sources
from src.services.providers.llm.factory import make_llm_provider
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider
from src.services.providers.vlm.factory import make_vlm_provider
from src.services.providers.vlm.ollama_provider import OllamaVLMProvider


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


class TestEmbeddingFactory:
    def test_default_is_local_huggingface(self):
        provider = make_embedding_provider(_settings())
        assert isinstance(provider, HuggingFaceEmbeddingProvider)
        assert provider.model_name == "BAAI/bge-m3"
        assert provider.dimension == 1024

    def test_ollama_provider_selected(self):
        settings = _settings(embedding=EmbeddingSettings(provider="ollama", model="bge-m3"))
        provider = make_embedding_provider(settings)
        assert isinstance(provider, OllamaEmbeddingProvider)

    def test_openai_compatible_provider_selected(self):
        settings = _settings(
            embedding=EmbeddingSettings(provider="openai-compatible", model="bge", endpoint="http://x/v1")
        )
        provider = make_embedding_provider(settings)
        assert isinstance(provider, OpenAICompatibleEmbeddingProvider)

    def test_unknown_provider_raises(self):
        settings = _settings(embedding=EmbeddingSettings(provider="does-not-exist"))
        with pytest.raises(ValueError):
            make_embedding_provider(settings)


class TestLLMFactory:
    def test_default_is_ollama(self):
        provider = make_llm_provider(_settings())
        assert isinstance(provider, OllamaClient)
        assert isinstance(provider, LLMProvider)

    def test_openai_compatible_selected(self):
        settings = _settings(llm=LLMSettings(provider="vllm", endpoint="http://x/v1", model="qwen"))
        provider = make_llm_provider(settings)
        assert isinstance(provider, OpenAICompatibleLLMProvider)

    def test_unknown_provider_raises(self):
        settings = _settings(llm=LLMSettings(provider="nope"))
        with pytest.raises(ValueError):
            make_llm_provider(settings)


class TestVLMFactory:
    def test_disabled_returns_none(self):
        assert make_vlm_provider(_settings()) is None

    def test_enabled_ollama(self):
        settings = _settings(vlm=VLMSettings(enabled=True, provider="ollama", model="qwen2.5-vl"))
        provider = make_vlm_provider(settings)
        assert isinstance(provider, OllamaVLMProvider)


class TestGenericCitations:
    def test_prefers_source_url_and_dedups(self):
        chunks = [
            {"source_url": "https://intranet/doc1", "title": "Doc 1"},
            {"source_url": "https://intranet/doc1", "title": "Doc 1"},
            {"title": "Doc 2"},
        ]
        assert build_sources(chunks) == ["https://intranet/doc1", "Doc 2"]

    def test_no_arxiv_assumption(self):
        # Falls back gracefully across generic identifiers; never fabricates URLs.
        chunks = [{"document_id": "kb-42"}, {"arxiv_id": "1706.03762"}]
        assert build_citations(chunks) == ["kb-42", "1706.03762"]

    def test_empty(self):
        assert build_sources([]) == []
        assert build_citations([]) == []
