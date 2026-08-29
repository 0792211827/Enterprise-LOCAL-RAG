"""Retrieval configuration endpoints."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import SessionDep
from src.models.retrieval import RetrievalConfiguration
from src.repositories import RetrievalConfigurationRepository
from src.schemas.api.domain import RetrievalConfigCreate, RetrievalConfigResponse

router = APIRouter(prefix="/api/v1/retrieval-configurations", tags=["retrieval"])


@router.get("", response_model=List[RetrievalConfigResponse])
def list_retrieval_configurations(session: SessionDep, limit: int = 100, offset: int = 0):
    repo = RetrievalConfigurationRepository(session)
    return repo.get_all(limit=limit, offset=offset)


@router.post("", response_model=RetrievalConfigResponse, status_code=status.HTTP_201_CREATED)
def create_retrieval_configuration(payload: RetrievalConfigCreate, session: SessionDep):
    repo = RetrievalConfigurationRepository(session)
    config = RetrievalConfiguration(
        name=payload.name,
        mode=payload.mode.value,
        top_k=payload.top_k,
        hybrid_size_multiplier=payload.hybrid_size_multiplier,
        rrf_rank_constant=payload.rrf_rank_constant,
        score_threshold=payload.score_threshold,
        filters=payload.filters,
    )
    return repo.create(config)


@router.get("/{config_id}", response_model=RetrievalConfigResponse)
def get_retrieval_configuration(config_id: UUID, session: SessionDep):
    repo = RetrievalConfigurationRepository(session)
    config = repo.get_by_id(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Retrieval configuration not found")
    return config


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_retrieval_configuration(config_id: UUID, session: SessionDep):
    repo = RetrievalConfigurationRepository(session)
    config = repo.get_by_id(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Retrieval configuration not found")
    repo.delete(config)
    return None
