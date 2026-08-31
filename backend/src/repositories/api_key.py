from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models.api_key import ApplicationApiKey, generate_api_key, hash_api_key


class ApiKeyRepository:
    """Persistence operations for application API keys."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, application_id: UUID, name: Optional[str] = None) -> tuple[ApplicationApiKey, str]:
        """Mint and persist a key, returning it alongside the one-time plaintext."""
        raw, key_hash, key_prefix, key_last4 = generate_api_key()
        key = ApplicationApiKey(
            application_id=application_id,
            name=name,
            key_prefix=key_prefix,
            key_last4=key_last4,
            key_hash=key_hash,
        )
        self.session.add(key)
        self.session.commit()
        self.session.refresh(key)
        return key, raw

    def list_for_application(self, application_id: UUID) -> List[ApplicationApiKey]:
        stmt = (
            select(ApplicationApiKey)
            .where(ApplicationApiKey.application_id == application_id)
            .order_by(ApplicationApiKey.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def get_by_id(self, key_id: UUID) -> Optional[ApplicationApiKey]:
        return self.session.get(ApplicationApiKey, key_id)

    def get_by_raw_key(self, raw: str) -> Optional[ApplicationApiKey]:
        """Look up an active key by its plaintext value."""
        stmt = select(ApplicationApiKey).where(ApplicationApiKey.key_hash == hash_api_key(raw))
        return self.session.scalar(stmt)

    def touch(self, key: ApplicationApiKey) -> None:
        """Record that the key was just used."""
        key.last_used_at = datetime.now(timezone.utc)
        self.session.commit()

    def revoke(self, key: ApplicationApiKey) -> ApplicationApiKey:
        if key.revoked_at is None:
            key.revoked_at = datetime.now(timezone.utc)
            self.session.commit()
            self.session.refresh(key)
        return key
