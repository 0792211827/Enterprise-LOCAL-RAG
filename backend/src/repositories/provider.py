from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.provider import ModelConfiguration, ModelProvider


class ProviderRepository:
    """Persistence operations for model providers and configurations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, provider: ModelProvider) -> ModelProvider:
        self.session.add(provider)
        self.session.commit()
        self.session.refresh(provider)
        return provider

    def get_by_id(self, provider_id: UUID) -> Optional[ModelProvider]:
        return self.session.get(ModelProvider, provider_id)

    def get_all(self, kind: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[ModelProvider]:
        stmt = select(ModelProvider)
        if kind is not None:
            stmt = stmt.where(ModelProvider.kind == kind)
        stmt = stmt.order_by(ModelProvider.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(ModelProvider)) or 0

    def update(self, provider: ModelProvider, data: dict) -> ModelProvider:
        for key, value in data.items():
            if value is not None and hasattr(provider, key):
                setattr(provider, key, value)
        self.session.commit()
        self.session.refresh(provider)
        return provider

    def delete(self, provider: ModelProvider) -> None:
        self.session.delete(provider)
        self.session.commit()

    # -- model configurations --------------------------------------------
    def create_configuration(self, configuration: ModelConfiguration) -> ModelConfiguration:
        self.session.add(configuration)
        self.session.commit()
        self.session.refresh(configuration)
        return configuration

    def get_configuration(self, configuration_id: UUID) -> Optional[ModelConfiguration]:
        return self.session.get(ModelConfiguration, configuration_id)
