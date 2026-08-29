from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.application import RAGApplication
from src.models.knowledge_base import KnowledgeBase


class ApplicationRepository:
    """Persistence operations for RAG applications."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, application: RAGApplication) -> RAGApplication:
        self.session.add(application)
        self.session.commit()
        self.session.refresh(application)
        return application

    def get_by_id(self, application_id: UUID) -> Optional[RAGApplication]:
        return self.session.get(RAGApplication, application_id)

    def get_by_slug(self, slug: str) -> Optional[RAGApplication]:
        return self.session.scalar(select(RAGApplication).where(RAGApplication.slug == slug))

    def get_all(self, limit: int = 100, offset: int = 0) -> List[RAGApplication]:
        stmt = select(RAGApplication).order_by(RAGApplication.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(RAGApplication)) or 0

    def set_knowledge_bases(self, application: RAGApplication, kb_ids: List[UUID]) -> None:
        kbs = [self.session.get(KnowledgeBase, kb_id) for kb_id in kb_ids]
        application.knowledge_bases = [kb for kb in kbs if kb is not None]
        self.session.commit()
        self.session.refresh(application)

    def update(self, application: RAGApplication, data: dict) -> RAGApplication:
        for key, value in data.items():
            if value is not None and hasattr(application, key):
                setattr(application, key, value)
        self.session.commit()
        self.session.refresh(application)
        return application

    def delete(self, application: RAGApplication) -> None:
        self.session.delete(application)
        self.session.commit()
