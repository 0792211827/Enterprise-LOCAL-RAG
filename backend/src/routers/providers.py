"""Model provider management endpoints (LLM / Embedding / VLM)."""
import time
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import SessionDep
from src.models.enums import ProviderKind
from src.models.provider import ModelProvider
from src.repositories import ProviderRepository
from src.schemas.api.domain import (
    ProviderCreate,
    ProviderResponse,
    ProviderTestResult,
    ProviderUpdate,
)

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


@router.get("", response_model=List[ProviderResponse])
def list_providers(session: SessionDep, kind: Optional[str] = None, limit: int = 100, offset: int = 0):
    repo = ProviderRepository(session)
    return repo.get_all(kind=kind, limit=limit, offset=offset)


@router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderCreate, session: SessionDep):
    repo = ProviderRepository(session)
    provider = ModelProvider(
        name=payload.name,
        kind=payload.kind.value,
        provider_type=payload.provider_type.value,
        endpoint=payload.endpoint,
        model=payload.model,
        api_key_ref=payload.api_key_ref,
        dimension=payload.dimension,
        capabilities=payload.capabilities,
        enabled=payload.enabled,
    )
    return repo.create(provider)


@router.get("/{provider_id}", response_model=ProviderResponse)
def get_provider(provider_id: UUID, session: SessionDep):
    repo = ProviderRepository(session)
    provider = repo.get_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.patch("/{provider_id}", response_model=ProviderResponse)
def update_provider(provider_id: UUID, payload: ProviderUpdate, session: SessionDep):
    repo = ProviderRepository(session)
    provider = repo.get_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return repo.update(provider, payload.model_dump(exclude_unset=True))


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(provider_id: UUID, session: SessionDep):
    repo = ProviderRepository(session)
    provider = repo.get_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    repo.delete(provider)
    return None


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(provider_id: UUID, session: SessionDep):
    """Test connectivity to a configured provider using real backend calls."""
    repo = ProviderRepository(session)
    provider = repo.get_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return await _run_provider_test(provider)


async def _run_provider_test(provider: ModelProvider) -> ProviderTestResult:
    start = time.perf_counter()
    try:
        if provider.kind == ProviderKind.EMBEDDING.value:
            return await _test_embedding(provider, start)
        return await _test_llm(provider, start)
    except Exception as exc:  # noqa: BLE001 - report as failed test
        return ProviderTestResult(
            connected=False,
            message="Unable to connect to the configured endpoint.",
            detail=str(exc),
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )


async def _test_llm(provider: ModelProvider, start: float) -> ProviderTestResult:
    from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

    if provider.provider_type == "ollama":
        from src.config import get_settings
        from src.services.ollama.client import OllamaClient

        settings = get_settings()
        client = OllamaClient(settings)
        health = await client.health_check()
        models = await client.list_models()
    else:
        client = OpenAICompatibleLLMProvider(
            endpoint=provider.endpoint or "",
            model=provider.model,
            api_key=provider.api_key_ref or None,
        )
        health = await client.health_check()
        models = await client.list_models()

    model_names = {m.get("name") or m.get("model") or m.get("id") for m in (models or [])}
    latency = round((time.perf_counter() - start) * 1000, 2)
    return ProviderTestResult(
        connected=bool(health),
        model_available=provider.model in model_names if model_names else None,
        latency_ms=latency,
        message="Connected" if health else "Endpoint reachable but reported unhealthy",
    )


async def _test_embedding(provider: ModelProvider, start: float) -> ProviderTestResult:
    from src.config import get_settings
    from src.services.providers.embeddings.factory import make_embedding_provider

    settings = get_settings()
    embed_provider = make_embedding_provider(settings)
    vector = await embed_provider.embed_query("connection test")
    latency = round((time.perf_counter() - start) * 1000, 2)
    return ProviderTestResult(
        connected=True,
        embedding_ok=bool(vector),
        dimension=len(vector) if vector else None,
        latency_ms=latency,
        message="Embedding generated successfully",
    )
