"""Pydantic request/response schemas for the platform domain API.

These schemas define the public API contract consumed by the Admin Control
Plane frontend. They deliberately avoid leaking ORM internals; every endpoint
returns a typed model rather than an arbitrary dictionary.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from src.config import get_settings
from src.models.enums import ProviderKind, ProviderType, RAGStrategy, RetrievalMode


# Embedding defaults are read from the running configuration rather than
# hardcoded. Retrieval always embeds with the process-wide provider
# (`EmbeddingsDep`), so a literal default would persist -- and the admin UI
# would then display -- an embedding model the deployment never actually uses.
def _default_embedding_provider() -> str:
    return get_settings().embedding.provider


def _default_embedding_model() -> str:
    return get_settings().embedding.model


def _default_embedding_dimension() -> int:
    return get_settings().embedding.dimension


# --------------------------------------------------------------------------- #
# Knowledge bases
# --------------------------------------------------------------------------- #
class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    embedding_provider: str = Field(default_factory=_default_embedding_provider)
    embedding_model: str = Field(default_factory=_default_embedding_model)
    embedding_dimension: int = Field(default_factory=_default_embedding_dimension, ge=1, le=8192)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    default_top_k: int = Field(10, ge=1, le=100)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    retrieval_mode: Optional[RetrievalMode] = None
    default_top_k: Optional[int] = Field(None, ge=1, le=100)


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: Optional[str]
    index_name: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    retrieval_mode: str
    default_top_k: int
    document_count: int
    chunk_count: int
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


# --------------------------------------------------------------------------- #
# Documents & ingestion
# --------------------------------------------------------------------------- #
class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    title: str
    source_uri: Optional[str]
    content_type: Optional[str]
    file_size_bytes: Optional[int]
    status: str
    error: Optional[str]
    chunk_count: int
    parser_used: Optional[str]
    processed_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class IngestionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    status: str
    stage: Optional[str]
    error: Optional[str]
    stats: Optional[dict]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: Optional[datetime]


# --------------------------------------------------------------------------- #
# Model providers
# --------------------------------------------------------------------------- #
class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: ProviderKind
    provider_type: ProviderType
    endpoint: Optional[str] = None
    model: str = Field(..., min_length=1)
    api_key_ref: Optional[str] = None
    dimension: Optional[str] = None
    capabilities: Optional[dict] = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    api_key_ref: Optional[str] = None
    dimension: Optional[str] = None
    capabilities: Optional[dict] = None
    enabled: Optional[bool] = None


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: str
    provider_type: str
    endpoint: Optional[str]
    model: str
    dimension: Optional[str]
    capabilities: Optional[dict]
    enabled: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class ProviderTestResult(BaseModel):
    connected: bool
    model_available: Optional[bool] = None
    generation_ok: Optional[bool] = None
    embedding_ok: Optional[bool] = None
    dimension: Optional[int] = None
    latency_ms: Optional[float] = None
    message: str
    detail: Optional[str] = None


# --------------------------------------------------------------------------- #
# Retrieval configuration
# --------------------------------------------------------------------------- #
class RetrievalConfigCreate(BaseModel):
    name: str = Field(..., min_length=1)
    mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: int = Field(10, ge=1, le=100)
    hybrid_size_multiplier: int = Field(2, ge=1, le=10)
    rrf_rank_constant: int = Field(60, ge=1, le=1000)
    score_threshold: Optional[float] = None
    filters: Optional[dict] = None


class RetrievalConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    mode: str
    top_k: int
    hybrid_size_multiplier: int
    rrf_rank_constant: int
    score_threshold: Optional[float]
    filters: Optional[dict]


# --------------------------------------------------------------------------- #
# RAG applications
# --------------------------------------------------------------------------- #
class ApplicationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    rag_strategy: RAGStrategy = RAGStrategy.TRADITIONAL
    knowledge_base_ids: List[UUID] = Field(default_factory=list)

    # Model configuration (inline; a ModelConfiguration row is created).
    llm_provider: str = "ollama"
    llm_endpoint: Optional[str] = None
    llm_model: str = "llama3.2:1b"
    embedding_provider: str = Field(default_factory=_default_embedding_provider)
    embedding_model: str = Field(default_factory=_default_embedding_model)
    vlm_enabled: bool = False
    vlm_provider: Optional[str] = None
    vlm_model: Optional[str] = None

    # Retrieval configuration (inline; a RetrievalConfiguration row is created).
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: int = Field(8, ge=1, le=100)
    score_threshold: Optional[float] = None

    # Generation parameters.
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1, le=32768)
    streaming_enabled: bool = True
    citations_enabled: bool = True


class ApplicationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    rag_strategy: Optional[RAGStrategy] = None
    knowledge_base_ids: Optional[List[UUID]] = None
    streaming_enabled: Optional[bool] = None
    citations_enabled: Optional[bool] = None


class ModelConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    llm_provider: str
    llm_endpoint: Optional[str]
    llm_model: str
    embedding_provider: str
    embedding_model: str
    vlm_enabled: bool
    vlm_provider: Optional[str]
    vlm_model: Optional[str]
    generation_params: Optional[dict]


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: Optional[str]
    system_prompt: Optional[str]
    rag_strategy: str
    streaming_enabled: bool
    citations_enabled: bool
    knowledge_bases: List[KnowledgeBaseResponse] = Field(default_factory=list)
    model_configuration: Optional[ModelConfigurationResponse] = None
    retrieval_configuration: Optional[RetrievalConfigResponse] = None
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    # Populated only on the create response, where the auto-provisioned key's
    # plaintext is surfaced exactly once. Always null on reads.
    api_key: Optional[str] = None


class ApplicationAskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    top_k: Optional[int] = Field(None, ge=1, le=100)


class RetrievedSource(BaseModel):
    document_id: Optional[str] = None
    document_title: Optional[str] = None
    chunk_text: str
    score: float
    section_title: Optional[str] = None
    retrieval_method: Optional[str] = None


class ApplicationAskResponse(BaseModel):
    query: str
    answer: str
    sources: List[RetrievedSource] = Field(default_factory=list)
    search_mode: str
    chunks_used: int


# --------------------------------------------------------------------------- #
# System / monitoring
# --------------------------------------------------------------------------- #
class ComponentHealth(BaseModel):
    name: str
    status: str  # healthy | unhealthy | disabled | unknown
    latency_ms: Optional[float] = None
    version: Optional[str] = None
    detail: Optional[str] = None


class SystemHealthResponse(BaseModel):
    status: str
    components: List[ComponentHealth]


class GPUInfo(BaseModel):
    index: int
    name: str
    memory_total_mb: Optional[float] = None
    memory_used_mb: Optional[float] = None
    utilization_percent: Optional[float] = None
    temperature_c: Optional[float] = None


class GPUResponse(BaseModel):
    available: bool
    cuda_version: Optional[str] = None
    driver_version: Optional[str] = None
    gpus: List[GPUInfo] = Field(default_factory=list)
    message: Optional[str] = None


class DashboardStats(BaseModel):
    knowledge_bases: int
    documents: int
    applications: int
    providers: int
    chunks: int
    ingestion_jobs: int
    ingestion_jobs_by_status: dict


# --------------------------------------------------------------------------- #
# Application API keys
# --------------------------------------------------------------------------- #


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    name: Optional[str] = None
    key_prefix: str
    key_last4: str
    is_active: bool
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiKeyCreateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)


class ApiKeyCreateResponse(ApiKeyResponse):
    """Returned once at creation/rotation; ``key`` is never recoverable later."""

    key: str


# --------------------------------------------------------------------------- #
# Provider model discovery
# --------------------------------------------------------------------------- #


class ProviderModel(BaseModel):
    id: str
    name: str


class ProviderModelsResponse(BaseModel):
    reachable: bool
    models: List[ProviderModel] = Field(default_factory=list)
    detail: Optional[str] = None


# --------------------------------------------------------------------------- #
# OpenAI-compatible chat completions
# --------------------------------------------------------------------------- #


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI chat-completions contract that we honour.

    Unknown fields are ignored so standard SDK clients work unmodified.
    """

    model_config = ConfigDict(extra="ignore")

    model: str
    messages: List[ChatMessage] = Field(..., min_length=1)
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage)
