"""API keys issued per RAG application for the OpenAI-compatible endpoint."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy import Column, DateTime, ForeignKey, String
from src.db.interfaces.postgresql import Base
from src.db.types import GUID

KEY_PREFIX_LENGTH = 11


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_api_key() -> Tuple[str, str, str, str]:
    """Mint a new API key.

    Only the hash is persisted; the plaintext is shown to the caller once and
    cannot be recovered afterwards.

    :returns: ``(raw_key, key_hash, key_prefix, key_last4)``
    """
    raw = f"sk-{secrets.token_urlsafe(32)}"
    return raw, hash_api_key(raw), raw[:KEY_PREFIX_LENGTH], raw[-4:]


def hash_api_key(raw: str) -> str:
    """Return the storage hash for a raw API key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApplicationApiKey(Base):
    """A bearer credential bound to a single RAG application."""

    __tablename__ = "application_api_keys"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    application_id = Column(
        GUID(),
        ForeignKey("rag_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=True)

    # Display-only fragments so the UI can render sk-abc...wxyz without the secret.
    key_prefix = Column(String, nullable=False)
    key_last4 = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True, index=True)

    created_at = Column(DateTime, default=_utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
