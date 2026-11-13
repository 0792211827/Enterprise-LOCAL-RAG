"""The upload-area cleanup command."""

import os
from datetime import datetime, timedelta, timezone

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from src.cli import upload_cleanup
from src.models import KnowledgeBase
from src.models.document import Document
from src.models.enums import IngestionStatus

from tests.audit_support import events, opener, sqlite_session_factory

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def age(path, days):
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


@pytest.fixture
def area(tmp_path):
    factory = sqlite_session_factory()
    root = tmp_path / "rag-uploads"
    (root / "kb").mkdir(parents=True)
    with factory() as session:
        kb = KnowledgeBase(name="kb", slug="kb", index_name="idx")
        session.add(kb)
        session.commit()

        def add(name, days, status, document=True):
            path = root / "kb" / name
            path.write_bytes(b"%PDF")
            age(path, days)
            if document:
                session.add(
                    Document(
                        knowledge_base_id=kb.id, title=name, file_path=str(path), checksum=name, status=status.value
                    )
                )
            return path

        files = {
            "old_done": add("old_done", 40, IngestionStatus.COMPLETED),
            "old_failed": add("old_failed", 40, IngestionStatus.FAILED),
            "old_processing": add("old_processing", 40, IngestionStatus.PROCESSING),
            "old_queued": add("old_queued", 40, IngestionStatus.QUEUED),
            "recent": add("recent", 3, IngestionStatus.COMPLETED),
            "orphan": add("orphan", 40, IngestionStatus.COMPLETED, document=False),
        }
        session.commit()
    return factory, root, files


def test_removes_only_stale_finished_and_orphaned_files(area):
    factory, root, files = area
    result = upload_cleanup.cleanup(opener(factory), root, days=30, now=NOW)

    assert result.removed == 3  # old_done, old_failed, orphan
    assert not files["old_done"].exists() and not files["old_failed"].exists() and not files["orphan"].exists()
    # Recent files, and files an ingestion may still need, stay.
    assert files["recent"].exists() and files["old_processing"].exists() and files["old_queued"].exists()


def test_documents_lose_their_dangling_path(area):
    factory, root, _ = area
    upload_cleanup.cleanup(opener(factory), root, days=30, now=NOW)
    with factory() as session:
        paths = {d.title: d.file_path for d in session.query(Document)}
    assert paths["old_done"] is None and paths["old_failed"] is None
    assert paths["recent"] is not None and paths["old_processing"] is not None


def test_dry_run_changes_nothing(area):
    factory, root, files = area
    result = upload_cleanup.cleanup(opener(factory), root, days=30, dry_run=True, now=NOW)
    assert result.removed == 3 and all(path.exists() for path in files.values())


def test_a_cleanup_leaves_an_audit_event(area):
    factory, root, _ = area
    upload_cleanup.cleanup(opener(factory), root, days=30, now=NOW)
    [event] = [e for e in events(factory) if e.action == "uploads.cleanup"]
    assert event.details["removed"] == 3 and event.details["days"] == 30 and event.auth_method == "cli"


def test_a_missing_upload_area_is_not_an_error(tmp_path):
    factory = sqlite_session_factory()
    assert upload_cleanup.cleanup(opener(factory), tmp_path / "nope", days=30, now=NOW).removed == 0


def test_days_must_be_positive(area):
    factory, root, _ = area
    with pytest.raises(ValueError):
        upload_cleanup.cleanup(opener(factory), root, days=0)


def test_cli_does_nothing_by_default(capsys):
    # INGESTION__UPLOAD_RETENTION_DAYS defaults to 0 (off) and no --days given: nothing is touched.
    assert upload_cleanup.main([]) == 2
    assert "off" in capsys.readouterr().err


def test_files_are_judged_by_age_not_by_directory_order(area):
    factory, root, files = area
    age(files["recent"], 31)
    upload_cleanup.cleanup(opener(factory), root, days=30, now=NOW)
    assert not files["recent"].exists()
