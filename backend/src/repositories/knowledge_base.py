from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRepository:
    """Persistence operations for knowledge bases."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        return knowledge_base

    def get_by_id(self, kb_id: UUID) -> Optional[KnowledgeBase]:
        return self.session.get(KnowledgeBase, kb_id)

    def get_by_slug(self, slug: str) -> Optional[KnowledgeBase]:
        return self.session.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == slug))

    def get_all(self, limit: int = 100, offset: int = 0) -> List[KnowledgeBase]:
        stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(KnowledgeBase)) or 0

    def update(self, knowledge_base: KnowledgeBase, data: dict) -> KnowledgeBase:
        for key, value in data.items():
            if value is not None and hasattr(knowledge_base, key):
                setattr(knowledge_base, key, value)
        self.session.commit()
        self.session.refresh(knowledge_base)
        return knowledge_base

    def delete(self, knowledge_base: KnowledgeBase) -> None:
        self.session.delete(knowledge_base)
        self.session.commit()
