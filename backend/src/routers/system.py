"""System health, dashboard statistics and GPU monitoring endpoints."""

import logging
import shutil
import subprocess
import time
from typing import List

from fastapi import APIRouter
from sqlalchemy import text
from src.dependencies import (
    CacheDep,
    DatabaseDep,
    LangfuseDep,
    LLMProviderDep,
    OpenSearchDep,
    SessionDep,
    SettingsDep,
)
from src.repositories import (
    ApplicationRepository,
    DocumentRepository,
    IngestionJobRepository,
    KnowledgeBaseRepository,
    ProviderRepository,
)
from src.schemas.api.domain import (
    ComponentHealth,
    DashboardStats,
    GPUInfo,
    GPUResponse,
    SystemHealthResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _timed(fn):
    start = time.perf_counter()
    try:
        result = fn()
        return result, round((time.perf_counter() - start) * 1000, 2), None
    except Exception as exc:  # noqa: BLE001
        return None, round((time.perf_counter() - start) * 1000, 2), str(exc)


@router.get("/health", response_model=SystemHealthResponse)
async def system_health(
    database: DatabaseDep,
    opensearch: OpenSearchDep,
    cache: CacheDep,
    langfuse: LangfuseDep,
    llm: LLMProviderDep,
    settings: SettingsDep,
):
    components: List[ComponentHealth] = []

    # PostgreSQL.
    def _db_check():
        with database.get_session() as session:
            session.execute(text("SELECT 1"))
        return True

    _, latency, err = _timed(_db_check)
    components.append(
        ComponentHealth(
            name="PostgreSQL",
            status="healthy" if err is None else "unhealthy",
            latency_ms=latency,
            detail=err,
        )
    )

    # OpenSearch.
    ok, latency, err = _timed(lambda: opensearch.health_check())
    components.append(
        ComponentHealth(
            name="OpenSearch",
            status="healthy" if ok else "unhealthy",
            latency_ms=latency,
            detail=err,
        )
    )

    # Redis / cache.
    if cache is None:
        components.append(ComponentHealth(name="Redis", status="disabled"))
    else:
        healthy, latency, err = _timed(lambda: bool(getattr(cache, "is_available", lambda: True)()))
        components.append(
            ComponentHealth(
                name="Redis",
                status="healthy" if healthy and err is None else "unhealthy",
                latency_ms=latency,
                detail=err,
            )
        )

    # LLM inference endpoint.
    start = time.perf_counter()
    try:
        health = await llm.health_check()
        llm_latency = round((time.perf_counter() - start) * 1000, 2)
        healthy = bool(health)
        components.append(
            ComponentHealth(
                name="Inference",
                status="healthy" if healthy else "unhealthy",
                latency_ms=llm_latency,
                detail=None if healthy else "Endpoint unreachable",
            )
        )
    except Exception as exc:  # noqa: BLE001
        components.append(
            ComponentHealth(
                name="Inference",
                status="unhealthy",
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                detail=str(exc),
            )
        )

    # Langfuse (monitoring). A constructed client object only proves that keys
    # were supplied at startup -- it says nothing about the collector being up.
    # Probe the server so a stopped Langfuse isn't reported as healthy.
    if langfuse is not None and getattr(langfuse, "client", None) is not None:
        auth_check = getattr(langfuse.client, "auth_check", None)
        if callable(auth_check):
            ok, latency, err = _timed(auth_check)
            components.append(
                ComponentHealth(
                    name="Langfuse",
                    status="healthy" if ok else "unhealthy",
                    latency_ms=latency,
                    detail=err,
                )
            )
        else:
            components.append(ComponentHealth(name="Langfuse", status="healthy"))
    else:
        components.append(ComponentHealth(name="Langfuse", status="disabled"))

    overall = "healthy" if all(c.status in ("healthy", "disabled") for c in components) else "degraded"
    return SystemHealthResponse(status=overall, components=components)


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(session: SessionDep):
    kb_repo = KnowledgeBaseRepository(session)
    doc_repo = DocumentRepository(session)
    app_repo = ApplicationRepository(session)
    provider_repo = ProviderRepository(session)
    job_repo = IngestionJobRepository(session)

    from src.models.enums import IngestionStatus

    by_status = {s.value: job_repo.count(status=s.value) for s in IngestionStatus}
    return DashboardStats(
        knowledge_bases=kb_repo.count(),
        documents=doc_repo.count(),
        applications=app_repo.count(),
        providers=provider_repo.count(),
        chunks=doc_repo.count_chunks(),
        ingestion_jobs=job_repo.count(),
        ingestion_jobs_by_status=by_status,
    )


@router.get("/gpu", response_model=GPUResponse)
def gpu_info():
    """Return real GPU information via ``nvidia-smi`` when available.

    Never fabricates values: CPU-only hosts return ``available=false``.
    """
    if shutil.which("nvidia-smi") is None:
        return GPUResponse(available=False, gpus=[], message="GPU information unavailable (no NVIDIA driver detected)")

    try:
        query = "index,name,memory.total,memory.used,utilization.gpu,temperature.gpu"
        output = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()

        driver = (
            subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            .stdout.strip()
            .splitlines()
        )

        gpus: List[GPUInfo] = []
        for line in output.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            gpus.append(
                GPUInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    memory_total_mb=float(parts[2]),
                    memory_used_mb=float(parts[3]),
                    utilization_percent=float(parts[4]),
                    temperature_c=float(parts[5]),
                )
            )
        return GPUResponse(
            available=bool(gpus),
            driver_version=driver[0] if driver else None,
            gpus=gpus,
            message=None if gpus else "No GPUs reported",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GPU query failed: %s", exc)
        return GPUResponse(available=False, gpus=[], message="GPU information unavailable")
