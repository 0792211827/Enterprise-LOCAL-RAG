import os
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).parent.parent  # <repo>/backend, or /app inside the image
REPO_ROOT = BACKEND_ROOT.parent

# Later entries win. Covers being launched from the repo root, from backend/, and
# from the container (where compose injects the vars directly and no file exists).
# Missing paths are skipped.
ENV_FILES = [".env", str(REPO_ROOT / ".env"), str(BACKEND_ROOT / ".env")]


class BaseConfigSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        extra="ignore",
        frozen=True,
        env_nested_delimiter="__",
        case_sensitive=False,
    )


class EmbeddingSettings(BaseConfigSettings):
    """Local-first embedding model configuration.

    Provider-driven so switching between local HuggingFace, Ollama, or an
    OpenAI-compatible endpoint never requires source changes. Defaults are
    fully local with no external API key.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="EMBEDDING__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    provider: str = "huggingface"  # huggingface | ollama | openai-compatible
    model: str = "BAAI/bge-m3"
    dimension: int = 1024
    endpoint: str = "http://localhost:11434"  # used by ollama / openai-compatible
    api_key: str = ""  # optional; only for authenticated local endpoints
    device: str = ""  # "", "cpu", "cuda", "mps"; empty = auto-detect
    normalize: bool = True
    query_prompt: str = ""  # optional instruction prefix for queries
    passage_prompt: str = ""  # optional instruction prefix for passages


class LLMSettings(BaseConfigSettings):
    """Local-first text-generation (LLM) configuration."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="LLM__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    provider: str = "ollama"  # ollama | openai-compatible
    endpoint: str = "http://localhost:11434"
    model: str = "llama3.2:1b"
    api_key: str = ""  # optional; only for authenticated local endpoints
    timeout: int = 300


class VLMSettings(BaseConfigSettings):
    """Optional Vision-Language Model configuration (off by default)."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="VLM__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    enabled: bool = False
    provider: str = "ollama"  # ollama | openai-compatible
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5-vl"
    api_key: str = ""


class PDFParserSettings(BaseConfigSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="PDF_PARSER__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    max_pages: int = 30
    max_file_size_mb: int = 20
    do_ocr: bool = False
    do_table_structure: bool = True


class ChunkingSettings(BaseConfigSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="CHUNKING__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    chunk_size: int = 600  # Target words per chunk
    overlap_size: int = 100  # Words to overlap between chunks
    min_chunk_size: int = 100  # Minimum words for a valid chunk
    section_based: bool = True  # Use section-based chunking when available


class OpenSearchSettings(BaseConfigSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="OPENSEARCH__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    host: str = "http://localhost:9200"
    index_name: str = "rag-documents"
    chunk_index_suffix: str = "chunks"  # Creates single hybrid index: {index_name}-{suffix}
    max_text_size: int = 1000000

    # Vector search settings
    vector_dimension: int = 1024  # Jina embeddings dimension
    vector_space_type: str = "cosinesimil"  # cosinesimil, l2, innerproduct

    # Hybrid search settings
    rrf_pipeline_name: str = "hybrid-rrf-pipeline"
    hybrid_search_size_multiplier: int = 2  # Get k*multiplier for better recall


class LangfuseSettings(BaseConfigSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="LANGFUSE__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    public_key: str = ""
    secret_key: str = ""
    host: str = "http://localhost:3000"  # Self-hosted Langfuse URL
    enabled: bool = True
    flush_at: int = 15  # Number of events before flushing
    flush_interval: float = 1.0  # Seconds between flushes
    max_retries: int = 3
    timeout: int = 30
    debug: bool = False


class RedisSettings(BaseConfigSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_prefix="REDIS__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    decode_responses: bool = True
    socket_timeout: int = 30
    socket_connect_timeout: int = 30

    # Cache settings
    ttl_hours: int = 6  # Cache TTL in hours


class Settings(BaseConfigSettings):
    app_version: str = "0.1.0"
    debug: bool = True
    environment: Literal["development", "staging", "production"] = "development"
    service_name: str = "rag-api"

    postgres_database_url: str = "postgresql://rag_user:rag_password@localhost:5432/rag_db"
    postgres_echo_sql: bool = False
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 0

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    ollama_timeout: int = 300

    pdf_parser: PDFParserSettings = Field(default_factory=PDFParserSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    opensearch: OpenSearchSettings = Field(default_factory=OpenSearchSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    vlm: VLMSettings = Field(default_factory=VLMSettings)

    @field_validator("postgres_database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not (v.startswith("postgresql://") or v.startswith("postgresql+psycopg2://")):
            raise ValueError("Database URL must start with 'postgresql://' or 'postgresql+psycopg2://'")
        return v


def get_settings() -> Settings:
    return Settings()
