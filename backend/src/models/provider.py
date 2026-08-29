import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, String, Text
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

from .enums import ProviderKind, ProviderType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelProvider(Base):
    """A configured, self-hosted model provider (LLM, embedding, or VLM).

    Stores connection details for a local inference endpoint. Secrets are held
    by reference (``api_key_ref``) rather than inline, so no credentials are
    persisted in plaintext within the platform database.
    """

    __tablename__ = "model_providers"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, index=True)  # ProviderKind
    provider_type = Column(String, nullable=False)  # ProviderType

    endpoint = Column(String, nullable=True)
    model = Column(String, nullable=False)
    # Reference (env var name / secret id) to an API key, never the raw secret.
    api_key_ref = Column(String, nullable=True)
    dimension = Column(String, nullable=True)  # embedding dimension when applicable
    capabilities = Column(JSON, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ModelConfiguration(Base):
    """A named bundle of LLM/embedding/VLM providers used by an application."""

    __tablename__ = "model_configurations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)

    llm_provider = Column(String, nullable=False, default="ollama")
    llm_endpoint = Column(String, nullable=True)
    llm_model = Column(String, nullable=False, default="llama3.2:1b")

    embedding_provider = Column(String, nullable=False, default="huggingface")
    embedding_model = Column(String, nullable=False, default="BAAI/bge-m3")

    vlm_enabled = Column(Boolean, nullable=False, default=False)
    vlm_provider = Column(String, nullable=True)
    vlm_model = Column(String, nullable=True)

    # Generation parameters (temperature, top_p, max_tokens, ...).
    generation_params = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
