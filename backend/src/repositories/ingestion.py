from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.ingestion import IngestionJob


class IngestionJobRepository:
    """Persistence operations for ingestion jobs."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, job: IngestionJob) -> IngestionJob:
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def get_by_id(self, job_id: UUID) -> Optional[IngestionJob]:
        return self.session.get(IngestionJob, job_id)

    def get_for_document(self, document_id: UUID) -> Optional[IngestionJob]:
        stmt = (
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(IngestionJob.created_at.desc())
        )
        return self.session.scalar(stmt)

    def get_all(self, limit: int = 100, offset: int = 0) -> List[IngestionJob]:
        stmt = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def count(self, status: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(IngestionJob)
        if status is not None:
            stmt = stmt.where(IngestionJob.status == status)
        return self.session.scalar(stmt) or 0

    def save(self, job: IngestionJob) -> IngestionJob:
        self.session.commit()
        self.session.refresh(job)
        return job
