"""Request and response schemas for Connector definitions."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"  # typed on a command line, so no spaces
ENV_REFERENCE = r"^env:[A-Za-z_][A-Za-z0-9_]*$"  # the only form a credential may take: a name, never the secret


class ConnectorCreate(BaseModel):
    """Map one source folder to one Knowledge Base."""

    name: str = Field(..., min_length=1, max_length=100, pattern=NAME_PATTERN, description="Used on the command line")
    knowledge_base_id: UUID
    source_type: Literal["path", "s3"] = "path"
    enabled: bool = True

    # path: a mounted folder, at or below CONNECTOR__MOUNT_ROOT
    path: Optional[str] = Field(None, max_length=1000)

    # s3: any S3-compatible service (AWS S3, MinIO, ...). Credentials are environment references only.
    endpoint_url: Optional[str] = Field(None, max_length=500, description="e.g. https://minio.internal:9000")
    bucket: Optional[str] = Field(None, max_length=255)
    prefix: Optional[str] = Field(None, max_length=1000, description="Only objects under this prefix are mirrored")
    region: Optional[str] = Field(None, max_length=100)
    access_key_ref: Optional[str] = Field(None, pattern=ENV_REFERENCE, description="env:NAME of the access key")
    secret_key_ref: Optional[str] = Field(None, pattern=ENV_REFERENCE, description="env:NAME of the secret key")

    @model_validator(mode="after")
    def _source_is_complete(self) -> "ConnectorCreate":
        if self.source_type == "path":
            if not (self.path or "").strip():
                raise ValueError("path is required for a mounted-path Connector")
            if any((self.endpoint_url, self.bucket, self.access_key_ref, self.secret_key_ref)):
                raise ValueError("S3 settings do not apply to a mounted-path Connector")
        else:
            missing = [n for n in ("endpoint_url", "bucket", "access_key_ref", "secret_key_ref") if not (getattr(self, n) or "").strip()]
            if missing:
                raise ValueError(f"an S3 Connector needs {', '.join(missing)} (credentials as env:NAME references)")
            if self.path:
                raise ValueError("path does not apply to an S3 Connector")
        return self


class ConnectorUpdate(BaseModel):
    enabled: Optional[bool] = None
    path: Optional[str] = Field(None, max_length=1000)
    endpoint_url: Optional[str] = Field(None, max_length=500)
    bucket: Optional[str] = Field(None, max_length=255)
    prefix: Optional[str] = Field(None, max_length=1000)
    region: Optional[str] = Field(None, max_length=100)
    access_key_ref: Optional[str] = Field(None, pattern=ENV_REFERENCE)
    secret_key_ref: Optional[str] = Field(None, pattern=ENV_REFERENCE)


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
