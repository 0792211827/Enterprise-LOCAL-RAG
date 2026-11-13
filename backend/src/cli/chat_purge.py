"""Delete employee Conversations past retention.

The API process already runs this once a day when CHAT__RETENTION_DAYS is above zero; this command is for
running it on demand (or from cron) and for a dry run.

    python -m src.cli.chat_purge                # uses CHAT__RETENTION_DAYS
    python -m src.cli.chat_purge --days 30
    python -m src.cli.chat_purge --days 30 --dry-run

or ``make chat-purge DAYS=30`` against the running ``rag-api`` container. Safe to run repeatedly. A run that
deletes anything leaves a ``chat.purge`` audit event with counts only.
"""

import argparse
import logging
import sys
from typing import Optional

from src.services.chat import purge_expired


def main(argv: Optional[list] = None) -> int:
    from src.config import get_settings
    from src.db.factory import make_database

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, help="Delete Conversations idle longer than this (default: CHAT__RETENTION_DAYS)")
    parser.add_argument("--dry-run", action="store_true", help="Only count what would be deleted")
    args = parser.parse_args(argv)

    days = args.days if args.days is not None else get_settings().chat.retention_days
    if days < 1:
        print("Nothing to do: CHAT__RETENTION_DAYS is 0 (keep forever). Pass --days N to purge explicitly.", file=sys.stderr)
        return 2

    database = make_database()
    try:
        count = purge_expired(database.get_session, days, dry_run=args.dry_run)
        verb = "Would delete" if args.dry_run else "Deleted"
        print(f"{verb} {count} Conversation(s) idle for more than {days} day(s).")
    finally:
        database.teardown()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
