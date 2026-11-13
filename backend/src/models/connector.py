"""Connectors: a source folder mirrored into one Knowledge Base."""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import backref, relationship
from src.db.interfaces.postgresql import Base
from src.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConnectorSourceType(str, Enum):
    PATH = "path"  # a folder mounted by the operating system (SMB, NFS, a bind mount)
    S3 = "s3"  # an S3-compatible bucket


class FileSyncStatus(str, Enum):
    SYNCED = "synced"
    FAILED = "failed"  # the last attempt failed; the file is retried on the next run


class Connector(Base):
    """One source folder mapped to one Knowledge Base.

    ``source_config`` holds where to read (a path; or an endpoint, bucket and prefix) and, for credentials, only
    ``env:NAME`` references -- never a secret.
    """

    __tablename__ = "connectors"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    knowledge_base_id = Column(GUID(), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type = Column(String, nullable=False)
    source_config = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)

    # The run in progress (a lock against two runs at once) and the last finished run.
    run_started_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String, nullable=True)  # ok | partial | error
    last_run_summary = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    created_by = Column(String, nullable=True)

    # Backrefs, as for grants, keep the existing models untouched while ORM deletes cascade.
    knowledge_base = relationship("KnowledgeBase", backref=backref("connectors", cascade="all, delete-orphan"))


class ConnectorFileState(Base):
    """What a Connector last saw of one source file, so a run can tell added, changed, unchanged and removed apart."""

    __tablename__ = "connector_file_states"
    __table_args__ = (UniqueConstraint("connector_id", "source_key", name="uq_connector_file"),)

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    connector_id = Column(GUID(), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False, index=True)
    source_key = Column(String, nullable=False)  # the path below the source root, with "/" separators
    # A cheap change signal from the source (size and modified time, or the object's ETag).
    fingerprint = Column(String, nullable=False, default="")
    checksum = Column(String, nullable=True)  # sha256 of the content last ingested
    size_bytes = Column(Integer, nullable=True)
    document_id = Column(GUID(), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default=FileSyncStatus.SYNCED.value)
    error = Column(Text, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    connector = relationship("Connector", backref=backref("file_states", cascade="all, delete-orphan"))
