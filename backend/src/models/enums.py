"""Shared enumerations for the platform domain model."""
from enum import Enum


class IngestionStatus(str, Enum):
    """Lifecycle status for documents and ingestion jobs."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RAGStrategy(str, Enum):
    """Supported RAG orchestration strategies for an application."""

    TRADITIONAL = "traditional"
    AGENTIC = "agentic"


class RetrievalMode(str, Enum):
    """Retrieval strategy used by an application/knowledge base."""

    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


class ProviderKind(str, Enum):
    """The role a model provider fulfils."""

    LLM = "llm"
    EMBEDDING = "embedding"
    VLM = "vlm"


class ProviderType(str, Enum):
    """The concrete provider implementation."""

    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai-compatible"
    HUGGINGFACE = "huggingface"
