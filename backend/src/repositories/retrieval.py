from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models.retrieval import RetrievalConfiguration


class RetrievalConfigurationRepository:
    """Persistence operations for retrieval configurations."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, configuration: RetrievalConfiguration) -> RetrievalConfiguration:
        self.session.add(configuration)
        self.session.commit()
        self.session.refresh(configuration)
        return configuration

    def get_by_id(self, configuration_id: UUID) -> Optional[RetrievalConfiguration]:
        return self.session.get(RetrievalConfiguration, configuration_id)

    def get_all(self, limit: int = 100, offset: int = 0) -> List[RetrievalConfiguration]:
        stmt = select(RetrievalConfiguration).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def update(self, configuration: RetrievalConfiguration, data: dict) -> RetrievalConfiguration:
        for key, value in data.items():
            if value is not None and hasattr(configuration, key):
                setattr(configuration, key, value)
        self.session.commit()
        self.session.refresh(configuration)
        return configuration

    def delete(self, configuration: RetrievalConfiguration) -> None:
        self.session.delete(configuration)
        self.session.commit()
