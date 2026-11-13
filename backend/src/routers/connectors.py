"""Connector definitions (Admin Control Plane).

An admin maps a source folder to one Knowledge Base here. Nothing in this router reads the source: runs are made by the
separate ``python -m src.cli.connector`` job, so the API process stays stateless.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from src.dependencies import PrincipalDep, SessionDep, SettingsDep
from src.models.connector import Connector, ConnectorSourceType, FileSyncStatus
from src.repositories import KnowledgeBaseRepository
from src.repositories.connector import ConnectorRepository
from src.schemas.api.connector import ConnectorCreate, ConnectorResponse, ConnectorUpdate
from src.services.connectors import ConnectorError, check_mount_path

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def _view(connector: Connector, repo: ConnectorRepository) -> ConnectorResponse:
    counts = repo.file_counts(connector.id)
    return ConnectorResponse(
        id=connector.id,
        name=connector.name,
        knowledge_base_id=connector.knowledge_base_id,
        source_type=connector.source_type,
        source=connector.source_config or {},
        enabled=connector.enabled,
        running=connector.run_started_at is not None,
        last_run_at=connector.last_run_at,
        last_run_status=connector.last_run_status,
        last_run_summary=connector.last_run_summary,
        files_synced=counts[FileSyncStatus.SYNCED.value],
        files_failed=counts[FileSyncStatus.FAILED.value],
        created_at=connector.created_at,
    )


def _checked_path(path: str, settings) -> str:
    try:
        return check_mount_path(path.strip(), settings)
    except ConnectorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=List[ConnectorResponse])
def list_connectors(session: SessionDep):
    repo = ConnectorRepository(session)
    return [_view(c, repo) for c in repo.list_all()]


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
def create_connector(payload: ConnectorCreate, session: SessionDep, settings: SettingsDep, principal: PrincipalDep):
    """Map a folder to a Knowledge Base. The Documents it brings in are unrestricted until an admin restricts them."""
    if not KnowledgeBaseRepository(session).get_by_id(payload.knowledge_base_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    repo = ConnectorRepository(session)
    if repo.get_by_name(payload.name):
        raise HTTPException(status_code=409, detail=f"A connector named '{payload.name}' already exists")
    connector = repo.create(
        Connector(
            name=payload.name,
            knowledge_base_id=payload.knowledge_base_id,
            source_type=ConnectorSourceType.PATH.value,
            source_config={"path": _checked_path(payload.path or "", settings)},
            enabled=payload.enabled,
            created_by=principal.subject,
        )
    )
    return _view(connector, repo)


def _get(session, connector_id: UUID) -> Connector:
    connector = ConnectorRepository(session).get_by_id(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.get("/{connector_id}", response_model=ConnectorResponse)
def get_connector(connector_id: UUID, session: SessionDep):
    return _view(_get(session, connector_id), ConnectorRepository(session))


@router.patch("/{connector_id}", response_model=ConnectorResponse)
def update_connector(connector_id: UUID, payload: ConnectorUpdate, session: SessionDep, settings: SettingsDep):
    connector = _get(session, connector_id)
    repo = ConnectorRepository(session)
    if payload.enabled is not None:
        connector.enabled = payload.enabled
    if payload.path is not None:
        connector.source_config = {**(connector.source_config or {}), "path": _checked_path(payload.path, settings)}
    return _view(repo.save(connector), repo)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(connector_id: UUID, session: SessionDep):
    """Stop mirroring. The Documents already brought in stay in the Knowledge Base as ordinary Documents."""
    ConnectorRepository(session).delete(_get(session, connector_id))
    return None
