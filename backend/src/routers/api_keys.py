"""API key management for RAG applications.

Keys are issued, displayed and revocable, but ``/v1/chat/completions`` does not
currently reject requests carrying an unknown key -- see that router for the
enforcement caveat. The admin UI states this plainly rather than implying the
key is already a security control.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import SessionDep
from src.repositories import ApiKeyRepository, ApplicationRepository
from src.schemas.api.domain import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse

router = APIRouter(prefix="/api/v1/applications/{application_id}/api-keys", tags=["api-keys"])


def _require_application(session, application_id: UUID):
    application = ApplicationRepository(session).get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.get("", response_model=List[ApiKeyResponse])
def list_api_keys(application_id: UUID, session: SessionDep):
    _require_application(session, application_id)
    return ApiKeyRepository(session).list_for_application(application_id)


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(application_id: UUID, payload: ApiKeyCreateRequest, session: SessionDep):
    """Mint a key. The plaintext is returned here and never again."""
    _require_application(session, application_id)
    key, raw = ApiKeyRepository(session).create(application_id, name=payload.name)
    return ApiKeyCreateResponse(**ApiKeyResponse.model_validate(key).model_dump(), key=raw)


@router.post("/{key_id}/rotate", response_model=ApiKeyCreateResponse)
def rotate_api_key(application_id: UUID, key_id: UUID, session: SessionDep):
    """Revoke a key and issue a replacement in one step."""
    _require_application(session, application_id)
    repo = ApiKeyRepository(session)
    existing = repo.get_by_id(key_id)
    if not existing or existing.application_id != application_id:
        raise HTTPException(status_code=404, detail="API key not found")
    repo.revoke(existing)
    key, raw = repo.create(application_id, name=existing.name)
    return ApiKeyCreateResponse(**ApiKeyResponse.model_validate(key).model_dump(), key=raw)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(application_id: UUID, key_id: UUID, session: SessionDep):
    _require_application(session, application_id)
    repo = ApiKeyRepository(session)
    existing = repo.get_by_id(key_id)
    if not existing or existing.application_id != application_id:
        raise HTTPException(status_code=404, detail="API key not found")
    repo.revoke(existing)
    return None
