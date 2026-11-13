"""Purging Conversations past retention.

Retention counts from a Conversation's last message. The purge removes the Conversation and everything attached
to it (messages, ratings), is safe to run repeatedly, and works in batches so a long backlog does not hold one
huge transaction. Run daily by the API process (see ``run_daily``) and on demand by ``make chat-purge``.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, ContextManager, Optional

from sqlalchemy.orm import Session
from src.models import AuditAction, AuditEvent, AuditOutcome
from src.repositories import ConversationRepository
from src.repositories.chat import DELETE_BATCH

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 24 * 60 * 60


def purge_expired(
    open_session: Callable[[], ContextManager[Session]],
    days: int,
    now: Optional[datetime] = None,
    batch_size: int = DELETE_BATCH,
    dry_run: bool = False,
) -> int:
    """Delete Conversations idle for more than ``days`` days; returns how many were (or, dry run, would be) deleted.

    ``days == 0`` keeps everything. A run that deletes something leaves a ``chat.purge`` audit event with counts only.
    """
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    deleted = 0
    with open_session() as session:
        repo = ConversationRepository(session)
        if dry_run:
            return repo.count_expired(cutoff)
        while True:
            ids = repo.expired_ids(cutoff, limit=batch_size)
            if not ids:
                break
            deleted += repo.delete_many(ids)
        if deleted:
            session.add(
                AuditEvent(
                    action=AuditAction.CHAT_PURGE.value,
                    outcome=AuditOutcome.OK.value,
                    auth_method="system",
                    details={"cutoff": cutoff.isoformat(), "days": days, "deleted": deleted},
                )
            )
            session.commit()
    return deleted


async def run_daily(open_session: Callable[[], ContextManager[Session]], days: int, interval: float = PURGE_INTERVAL_SECONDS):
    """Purge now and then every ``interval`` seconds, until cancelled. A failed run is logged and retried next time."""
    while True:
        try:
            deleted = await asyncio.to_thread(purge_expired, open_session, days)
            if deleted:
                logger.info("Chat retention: purged %d Conversation(s) idle for more than %d days", deleted, days)
        except Exception:  # noqa: BLE001 - one bad run must not end the schedule
            logger.exception("Chat retention purge failed")
        await asyncio.sleep(interval)
