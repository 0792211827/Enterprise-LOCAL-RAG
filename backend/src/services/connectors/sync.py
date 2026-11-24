"""One Connector run: list the source, compare it with what was recorded, and make the Knowledge Base match.

* a file not seen before is **added** (ingested as a new Document);
* a file whose content changed is **updated** in place -- the same Document, so restrictions an admin set survive;
* a file that disappeared is **removed**, Document and indexed chunks both, so withdrawn content stops being answerable;
* anything else is left alone, and is not even read when its size and modified time (or ETag) are unchanged.

A file that fails is recorded and retried on the next run; it never stops the others.
"""

import getpass
import hashlib
import logging
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Dict, List, Optional

from sqlalchemy.orm import Session
from src.config import Settings
from src.models import AuditAction, AuditEvent, AuditOutcome
from src.models.connector import Connector, ConnectorFileState, ConnectorSourceType, FileSyncStatus
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.repositories import DocumentRepository, IngestionJobRepository, KnowledgeBaseRepository
from src.repositories.connector import ConnectorRepository
from src.services.ingestion.types import sniff_document_type, store_file, type_for_extension
from src.services.ingestion.uploads import display_name

from .sources import ConnectorError, Source, SourceFile

logger = logging.getLogger(__name__)

AUDIT_FILE_LIST_LIMIT = 50  # file names kept per kind in the audit event
ERROR_LENGTH = 300


def build_source(connector: Connector, settings: Settings) -> Source:
    """The source a Connector reads from."""
    config = connector.source_config or {}
    if connector.source_type == ConnectorSourceType.PATH.value:
        from .sources import PathSource

        return PathSource(config.get("path", ""), settings)
    if connector.source_type == ConnectorSourceType.S3.value:
        from .sources import S3Source

        return S3Source(config, settings)
    raise ConnectorError(f"Unknown source type '{connector.source_type}'.")


@dataclass
class SyncSummary:
    added: int = 0
    updated: int = 0
    removed: int = 0
    failed: int = 0
    unchanged: int = 0
    skipped: int = 0  # files of a type that cannot be ingested
    dry_run: bool = False
    files: Dict[str, List[Any]] = field(default_factory=lambda: {"added": [], "updated": [], "removed": [], "failed": []})

    def note(self, kind: str, key: str, error: Optional[str] = None) -> None:
        setattr(self, kind, getattr(self, kind) + 1)
        if kind in self.files:
            self.files[kind].append({"key": key, "error": error} if kind == "failed" else key)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "failed": self.failed,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
            "files": {kind: items[:AUDIT_FILE_LIST_LIMIT] for kind, items in self.files.items()},
        }

    def render(self) -> str:
        prefix = "Would have: " if self.dry_run else ""
        lines = [
            f"{prefix}added {self.added}, updated {self.updated}, removed {self.removed}, failed {self.failed} "
            f"({self.unchanged} unchanged, {self.skipped} skipped as an unsupported type)"
        ]
        for item in self.files["failed"]:
            lines.append(f"  FAILED {item['key']}: {item['error']}")
        return "\n".join(lines)


def _title(key: str) -> str:
    """A Document title from a source key: the folders are kept (names repeat across folders), made display-safe."""
    return "/".join(display_name(part, fallback="_") for part in key.split("/"))[:300]


class ConnectorSync:
    """Runs Connectors. Collaborators are injected so the same code runs in the job and in tests."""

    def __init__(
        self,
        open_session: Callable[[], ContextManager[Session]],
        ingestion: Any,
        opensearch: Any,
        settings: Settings,
        source_factory: Callable[[Connector, Settings], Source] = build_source,
    ):
        self.open_session = open_session
        self.ingestion = ingestion
        self.opensearch = opensearch
        self.settings = settings
        self.source_factory = source_factory

    async def run(self, name: str, dry_run: bool = False, allow_empty: bool = False) -> SyncSummary:
        """Sync the Connector called ``name``.

        :raises LookupError: no such Connector
        :raises ConnectorError: the run could not be made (disabled, already running, source unusable); a dry run
            changes and records nothing.
        """
        with self.open_session() as session:
            repo = ConnectorRepository(session)
            connector = repo.get_by_name(name)
            if connector is None:
                raise LookupError(f"No Connector named '{name}'.")
            if not connector.enabled:
                raise ConnectorError(f"Connector '{name}' is disabled.")
            if not dry_run and not repo.try_lock(connector, timedelta(minutes=self.settings.connector.lock_minutes)):
                raise ConnectorError(
                    f"Connector '{name}' is already running (started {connector.run_started_at}). "
                    f"A crashed run's lock expires after CONNECTOR__LOCK_MINUTES."
                )

            summary = SyncSummary(dry_run=dry_run)
            outcome, error = AuditOutcome.OK, None
            try:
                await self._sync(session, connector, summary, dry_run, allow_empty)
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the caller
                outcome, error = AuditOutcome.ERROR, str(exc)[:ERROR_LENGTH]
                raise
            finally:
                if not dry_run:
                    session.rollback()  # a failed file must not poison the bookkeeping below
                    status = "error" if error else "partial" if summary.failed else "ok"
                    details = {**summary.as_dict(), **({"error": error} if error else {})}
                    repo.finish(connector, status, details)
                    self._audit(session, connector, outcome, summary, error)
        return summary

    # ------------------------------------------------------------------------------------------------------------ #
    async def _sync(self, session: Session, connector: Connector, summary: SyncSummary, dry_run: bool, allow_empty: bool) -> None:
        repo = ConnectorRepository(session)
        kb = KnowledgeBaseRepository(session).get_by_id(connector.knowledge_base_id)
        if kb is None:
            raise ConnectorError("The Knowledge Base this Connector fills no longer exists.")

        source = self.source_factory(connector, self.settings)
        try:
            listed = source.list_files()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not list the source: {exc}") from exc
        files = [f for f in listed if type_for_extension(f.key) is not None]
        summary.skipped = len(listed) - len(files)

        states = repo.states(connector.id)
        live = {f.key for f in files}
        gone = [state for key, state in states.items() if key not in live]
        if gone and not live and not allow_empty:
            # An unmounted or emptied share looks exactly like a deleted one; wiping the Knowledge Base on a guess is worse.
            raise ConnectorError(
                f"The source lists no supported files but {len(gone)} were synced before, so nothing was removed. "
                "If the share really is empty, confirm with --allow-empty."
            )

        for source_file in files:
            await self._sync_file(session, connector, kb, source, source_file, states.get(source_file.key), summary, dry_run)
        for state in gone:
            self._remove(session, state, summary, dry_run)

        if not dry_run:
            doc_repo = DocumentRepository(session)
            kb.document_count, kb.chunk_count = doc_repo.count(kb.id), doc_repo.count_chunks(kb.id)
            KnowledgeBaseRepository(session).update(kb, {})

    async def _sync_file(
        self,
        session: Session,
        connector: Connector,
        kb: Any,
        source: Source,
        source_file: SourceFile,
        state: Optional[ConnectorFileState],
        summary: SyncSummary,
        dry_run: bool,
    ) -> None:
        key = source_file.key
        doc_repo = DocumentRepository(session)
        document = doc_repo.get_by_id(state.document_id) if state is not None and state.document_id else None
        in_step = (
            state is not None
            and document is not None
            and state.status == FileSyncStatus.SYNCED.value
            and state.fingerprint == source_file.fingerprint
        )
        if in_step:
            summary.note("unchanged", key)
            return
        try:
            cap = self.settings.ingestion.max_upload_mb
            if source_file.size > cap * 1024 * 1024:
                raise ValueError(f"larger than the {cap} MB limit (INGESTION__MAX_UPLOAD_MB)")
            raw = source.read(key)
            checksum = hashlib.sha256(raw).hexdigest()

            if state is not None and document is not None and state.status == FileSyncStatus.SYNCED.value and state.checksum == checksum:
                # Touched but not changed: remember the new signal so it is not read again.
                if not dry_run:
                    state.fingerprint = source_file.fingerprint
                    session.commit()
                summary.note("unchanged", key)
                return

            kind = "updated" if document is not None and state is not None and state.last_synced_at else "added"
            if not dry_run:
                await self._ingest(session, connector, kb, source_file, raw, checksum, state, document)
            summary.note(kind, key)
        except Exception as exc:  # noqa: BLE001 - one file never stops the run
            session.rollback()
            message = str(exc)[:ERROR_LENGTH] or exc.__class__.__name__
            logger.warning("Connector %s: %s failed: %s", connector.name, key, message)
            current = None if dry_run else ConnectorRepository(session).get_state(connector.id, key)
            if current is not None:  # which a failure inside _ingest may have just created
                current.status, current.error = FileSyncStatus.FAILED.value, message
                session.commit()
            summary.note("failed", key, message)

    async def _ingest(
        self,
        session: Session,
        connector: Connector,
        kb: Any,
        source_file: SourceFile,
        raw: bytes,
        checksum: str,
        state: Optional[ConnectorFileState],
        document: Optional[Document],
    ) -> None:
        repo = ConnectorRepository(session)
        doc_repo, job_repo = DocumentRepository(session), IngestionJobRepository(session)
        stored = store_file(self.settings, kb.id, checksum, sniff_document_type(source_file.key, raw), raw)

        fields = dict(
            title=_title(source_file.key),
            source_uri=f"connector:{connector.name}/{source_file.key}"[:500],
            content_type=stored.content_type,
            file_path=stored.file_path,
            file_size_bytes=len(raw),
            checksum=checksum,
            raw_text=stored.raw_text,
            sections=None,
            status=IngestionStatus.QUEUED.value,
            error=None,
            chunk_count=0,
        )
        if document is not None:
            # In place, so the Document keeps its identity -- and with it any restrictions an admin has set.
            self.opensearch.purge_document_chunks(str(document.id))
            doc_repo.delete_chunks_for_document(document.id)
            for name, value in fields.items():
                setattr(document, name, value)
            doc_repo.save(document)
        else:
            document = doc_repo.create(Document(knowledge_base_id=kb.id, **fields))

        # Recorded before ingesting, as failed, so a crash or failure leaves a state that is retried, never a lost file.
        if state is None:
            state = repo.add_state(
                ConnectorFileState(
                    connector_id=connector.id, source_key=source_file.key, document_id=document.id, status=FileSyncStatus.FAILED.value
                )
            )
        else:
            state.document_id, state.fingerprint, state.status = document.id, "", FileSyncStatus.FAILED.value
            session.commit()

        job = job_repo.create(IngestionJob(document_id=document.id, knowledge_base_id=kb.id, status=IngestionStatus.QUEUED.value))
        await self.ingestion.ingest_document(
            document=document, job=job, document_repo=doc_repo, ingestion_repo=job_repo, knowledge_base=kb
        )
        if document.status != IngestionStatus.COMPLETED.value:
            raise RuntimeError(document.error or "ingestion failed")

        state.fingerprint, state.checksum, state.size_bytes = source_file.fingerprint, checksum, len(raw)
        state.status, state.error, state.last_synced_at = FileSyncStatus.SYNCED.value, None, datetime.now(timezone.utc)
        session.commit()

    def _remove(self, session: Session, state: ConnectorFileState, summary: SyncSummary, dry_run: bool) -> None:
        key = state.source_key
        if dry_run:
            summary.note("removed", key)
            return
        try:
            doc_repo = DocumentRepository(session)
            document = doc_repo.get_by_id(state.document_id) if state.document_id else None
            if document is not None:
                # The index first: if the chunks cannot be removed the Document stays, and the next run tries again.
                self.opensearch.purge_document_chunks(str(document.id))
                doc_repo.delete(document)
            ConnectorRepository(session).delete_state(state)
            summary.note("removed", key)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            message = str(exc)[:ERROR_LENGTH] or exc.__class__.__name__
            logger.warning("Connector: removing %s failed: %s", key, message)
            summary.note("failed", key, f"could not remove: {message}")

    def _audit(self, session: Session, connector: Connector, outcome: AuditOutcome, summary: SyncSummary, error: Optional[str]) -> None:
        session.add(
            AuditEvent(
                action=AuditAction.CONNECTOR_SYNC.value,
                outcome=outcome.value,
                actor_subject=f"operator:{getpass.getuser()}@{socket.gethostname()}",
                auth_method="cli",
                resource_type="connector",
                resource_id=str(connector.id),
                requested_kb_ids=[str(connector.knowledge_base_id)],
                details={
                    "connector": connector.name,
                    "source_type": connector.source_type,
                    **summary.as_dict(),
                    **({"error": error} if error else {}),
                },
            )
        )
        session.commit()
