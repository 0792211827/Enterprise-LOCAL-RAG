"""Purging Conversations past retention.

Retention counts from a Conversation's last message. The purge removes the Conversation and everything attached
to it (messages), is safe to run repeatedly, and works in batches so a long backlog does not hold one
huge transaction. Run daily by the API process (see ``run_daily``) and on demand by ``make chat-purge``.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, List, Optional

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
    opensearch: Any = None,
) -> int:
    """Delete Conversations idle for more than ``days`` days; returns how many were (or, dry run, would be) deleted.

    ``days == 0`` keeps everything. A run that deletes something leaves a ``chat.purge`` audit event with counts only.

    A Conversation with chunks in the private partition of the chunk index is deleted only after those chunks are:
    if they cannot be removed (no ``opensearch`` given, or it is unreachable) it is left for the next run.
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
            removable = _without_stuck_partitions(repo, ids, opensearch)
            if not removable:
                break  # everything left in this batch is waiting for its chunks to be removable; retry next run
            deleted += repo.delete_many(removable)
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


def _without_stuck_partitions(repo: ConversationRepository, ids: List, opensearch: Any) -> List:
    """``ids`` minus the Conversations whose partition chunks could not be removed from the chunk index."""
    removable = []
    partitioned = set(repo.with_partitions(ids))
    for conversation_id in ids:
        if conversation_id in partitioned:
            try:
                if opensearch is None:
                    raise RuntimeError("no chunk index available")
                opensearch.delete_attachment_chunks(str(conversation_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Chat retention: kept a Conversation, its partition could not be removed (%s)", type(exc).__name__)
                continue
        removable.append(conversation_id)
    return removable


async def run_daily(
    open_session: Callable[[], ContextManager[Session]], days: int, interval: float = PURGE_INTERVAL_SECONDS, opensearch: Any = None
):
    """Purge now and then every ``interval`` seconds, until cancelled. A failed run is logged and retried next time."""
    while True:
        try:
            deleted = await asyncio.to_thread(purge_expired, open_session, days, opensearch=opensearch)
            if deleted:
                logger.info("Chat retention: purged %d Conversation(s) idle for more than %d days", deleted, days)
        except Exception:  # noqa: BLE001 - one bad run must not end the schedule
            logger.exception("Chat retention purge failed")
        await asyncio.sleep(interval)
