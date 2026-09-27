"""The audit purge command."""

from datetime import datetime, timedelta, timezone

import pytest
from src.cli import audit_purge
from src.models import AuditEvent

from tests.audit_support import events, opener, sqlite_session_factory

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture
def factory():
    session_factory = sqlite_session_factory()
    with session_factory() as session:
        for age in (400, 366, 10):
            session.add(AuditEvent(action="query.answer", outcome="ok", occurred_at=NOW - timedelta(days=age)))
        session.commit()
    return session_factory


def test_deletes_only_past_the_cutoff_and_leaves_a_tombstone(factory):
    assert audit_purge.purge(opener(factory), days=365, now=NOW) == 2
    remaining = events(factory)
    assert [e.action for e in remaining] == ["query.answer", "audit.purge"]
    tombstone = remaining[-1]
    assert tombstone.details["deleted"] == 2 and tombstone.details["days"] == 365
    assert tombstone.actor_subject.startswith("operator:") and tombstone.auth_method == "cli"


def test_dry_run_changes_nothing(factory):
    assert audit_purge.purge(opener(factory), days=365, dry_run=True, now=NOW) == 2
    assert len(events(factory)) == 3


def test_days_must_be_positive(factory):
    with pytest.raises(ValueError):
        audit_purge.purge(opener(factory), days=0)


def test_cli_refuses_when_retention_is_forever(capsys):
    # AUDIT__RETENTION_DAYS defaults to 0 and no --days given: no database is touched.
    assert audit_purge.main([]) == 2
    assert "keep forever" in capsys.readouterr().err
