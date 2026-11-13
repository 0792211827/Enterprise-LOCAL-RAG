"""Delete audit events older than the retention period.

There is deliberately no HTTP endpoint for this: removing audit history is an
operator action on the host, not something a stolen admin token can do.

    python -m src.cli.audit_purge                # uses AUDIT__RETENTION_DAYS
    python -m src.cli.audit_purge --days 365
    python -m src.cli.audit_purge --days 365 --dry-run

or ``make audit-purge DAYS=365`` against the running ``rag-api`` container.

Every purge leaves an ``audit.purge`` event (cutoff, rows deleted, the OS
user who ran it) -- even with AUDIT__LEVEL=off, since it changes the audit
record itself.
"""

import argparse
import getpass
import logging
import socket
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, ContextManager, Optional

from sqlalchemy.orm import Session
from src.models import AuditAction, AuditEvent, AuditOutcome
from src.repositories import AuditEventRepository

logger = logging.getLogger(__name__)


def purge(
    open_session: Callable[[], ContextManager[Session]],
    days: int,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> int:
    """Delete events older than ``days``; returns how many were (or would be) deleted."""
    if days < 1:
        raise ValueError("days must be at least 1")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)

    with open_session() as session:
        repo = AuditEventRepository(session)
        if dry_run:
            return repo.count_before(cutoff)
        deleted = repo.delete_before(cutoff)
        # Same transaction: the history is never gone without a record of it.
        session.add(
            AuditEvent(
                action=AuditAction.AUDIT_PURGE.value,
                outcome=AuditOutcome.OK.value,
                actor_subject=f"operator:{getpass.getuser()}@{socket.gethostname()}",
                auth_method="cli",
                details={"cutoff": cutoff.isoformat(), "days": days, "deleted": deleted},
            )
        )
        session.commit()
    return deleted


def main(argv: Optional[list] = None) -> int:
    from src.config import get_settings
    from src.db.factory import make_database

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, help="Delete events older than this (default: AUDIT__RETENTION_DAYS)")
    parser.add_argument("--dry-run", action="store_true", help="Only count what would be deleted")
    args = parser.parse_args(argv)

    days = args.days if args.days is not None else get_settings().audit.retention_days
    if days < 1:
        print(
            "Nothing to do: AUDIT__RETENTION_DAYS is 0 (keep forever). Pass --days N to purge explicitly.",
            file=sys.stderr,
        )
        return 2

    database = make_database()
    try:
        count = purge(database.get_session, days, dry_run=args.dry_run)
    finally:
        database.teardown()
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {count} audit event(s) older than {days} day(s).")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
