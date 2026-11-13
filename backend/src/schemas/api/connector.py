"""Request and response schemas for Connector definitions."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"  # typed on a command line, so no spaces


class ConnectorCreate(BaseModel):
    """Map one source folder to one Knowledge Base."""

    name: str = Field(..., min_length=1, max_length=100, pattern=NAME_PATTERN, description="Used on the command line")
    knowledge_base_id: UUID
    source_type: Literal["path"] = "path"
    path: Optional[str] = Field(None, max_length=1000, description="A mounted folder, at or below CONNECTOR__MOUNT_ROOT")
    enabled: bool = True

    @model_validator(mode="after")
    def _source_is_complete(self) -> "ConnectorCreate":
        if self.source_type == "path" and not (self.path or "").strip():
            raise ValueError("path is required for a mounted-path Connector")
        return self


class ConnectorUpdate(BaseModel):
    enabled: Optional[bool] = None
    path: Optional[str] = Field(None, max_length=1000)


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    knowledge_base_id: UUID
    source_type: str
    source: Dict[str, Any] = Field(description="Where it reads from; credentials appear only as env:NAME references")
    enabled: bool
    running: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    last_run_summary: Optional[Dict[str, Any]]
    files_synced: int
    files_failed: int
    created_at: Optional[datetime]
