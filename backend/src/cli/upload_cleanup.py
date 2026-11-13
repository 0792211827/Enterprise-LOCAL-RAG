"""Remove stale files from the upload area.

Uploaded binaries are kept only so they can be parsed; once a Document is
ingested its extracted text is stored and the file is no longer needed. Nothing
is removed automatically: this is an operator action, off unless asked for.

    python -m src.cli.upload_cleanup                # uses INGESTION__UPLOAD_RETENTION_DAYS
    python -m src.cli.upload_cleanup --days 14
    python -m src.cli.upload_cleanup --days 14 --dry-run

or ``make uploads-cleanup DAYS=14`` against the running ``rag-api`` container.

A file is removed when it is older than the cutoff and no queued or processing
Document still needs it. Documents that pointed at a removed file lose that
path (their extracted text stays; a reprocess needing the file asks for a new
upload). Each run leaves an ``uploads.cleanup`` audit event.
"""

import argparse
import getpass
import logging
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, ContextManager, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models import AuditAction, AuditEvent, AuditOutcome
from src.models.document import Document
from src.models.enums import IngestionStatus

logger = logging.getLogger(__name__)

_IN_USE = {IngestionStatus.QUEUED.value, IngestionStatus.PROCESSING.value}


@dataclass
class CleanupResult:
    removed: int = 0
    kept_in_use: int = 0
    bytes_freed: int = 0


def cleanup(
    open_session: Callable[[], ContextManager[Session]],
    root: Path,
    days: int,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> CleanupResult:
    """Remove files under ``root`` older than ``days``; returns what was (or would be) removed."""
    if days < 1:
        raise ValueError("days must be at least 1")
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).timestamp()
    result = CleanupResult()
    if not root.is_dir():
        return result

    with open_session() as session:
        documents = {
            Path(document.file_path).resolve(): document
            for document in session.scalars(select(Document).where(Document.file_path.is_not(None)))
        }
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
            if path.stat().st_mtime >= cutoff:
                continue
            document = documents.get(path.resolve())
            if document is not None and document.status in _IN_USE:
                result.kept_in_use += 1
                continue
            result.removed += 1
            result.bytes_freed += path.stat().st_size
            if dry_run:
                continue
            path.unlink()
            if document is not None:
                document.file_path = None

        if not dry_run:
            # Same transaction as the path updates: a removal is never unrecorded.
            session.add(
                AuditEvent(
                    action=AuditAction.UPLOADS_CLEANUP.value,
                    outcome=AuditOutcome.OK.value,
                    actor_subject=f"operator:{getpass.getuser()}@{socket.gethostname()}",
                    auth_method="cli",
                    details={
                        "days": days,
                        "removed": result.removed,
                        "kept_in_use": result.kept_in_use,
                        "bytes_freed": result.bytes_freed,
                    },
                )
            )
            session.commit()
    return result


def main(argv: Optional[list] = None) -> int:
    from src.config import get_settings
    from src.db.factory import make_database
    from src.services.ingestion.uploads import upload_root

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, help="Remove files older than this (default: INGESTION__UPLOAD_RETENTION_DAYS)")
    parser.add_argument("--dry-run", action="store_true", help="Only count what would be removed")
    args = parser.parse_args(argv)

    settings = get_settings()
    days = args.days if args.days is not None else settings.ingestion.upload_retention_days
    if days < 1:
        print(
            "Nothing to do: INGESTION__UPLOAD_RETENTION_DAYS is 0 (cleanup is off). Pass --days N to clean up explicitly.",
            file=sys.stderr,
        )
        return 2

    database = make_database()
    try:
        result = cleanup(database.get_session, upload_root(settings), days, dry_run=args.dry_run)
    finally:
        database.teardown()
    verb = "Would remove" if args.dry_run else "Removed"
    print(
        f"{verb} {result.removed} file(s) older than {days} day(s) "
        f"({result.bytes_freed / 1024 / 1024:.1f} MB); {result.kept_in_use} still in use were kept."
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
