"""Retention and privacy defaults for employee chat: a bounded life for stored Conversations, and no chat text in the
audit log or Langfuse unless an operator opts in (Langfuse only: the audit log never carries chat text).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from src.config import AuthSettings, ChatSettings, Settings
from src.models import AuditEvent, Conversation, ConversationMessage, MessageFeedback
from src.services.audit.recorder import sha256_text
from src.services.chat.retention import purge_expired
from tests.audit_support import events, opener
from tests.chat_support import exchange, stack, world  # noqa: F401

QUESTION = "what is bob's salary band"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_the_retention_default_is_ninety_days_and_zero_keeps_forever():
    assert ChatSettings().retention_days == 90
    assert ChatSettings(retention_days=0).retention_days == 0


# --------------------------------------------------------------------------- #
# Audit: metadata only
# --------------------------------------------------------------------------- #
def test_a_chat_audit_record_holds_metadata_never_the_question_or_the_answer_even_at_full_level(stack, world):
    exchange(stack, world, QUESTION)

    [event] = [e for e in events(world.factory) if e.action == "query.answer"]
    assert (event.query_text, event.answer_text) == (None, None)
    # Digests still let an auditor prove which question was asked, by hashing a candidate.
    assert event.query_sha256 == sha256_text(QUESTION) and event.answer_sha256 == sha256_text("25 days.")
    assert event.actor_subject == "alice" and event.retrieved[0]["chunk_id"] == "c1"
    everything = " ".join(str(v) for v in vars(event).values())
    assert "salary" not in everything and "25 days" not in everything


# --------------------------------------------------------------------------- #
# Langfuse: content only on opt-in
# --------------------------------------------------------------------------- #
def test_langfuse_traces_of_chat_omit_the_question_and_the_answer_by_default(stack, world):
    exchange(stack, world, QUESTION)

    sent = stack.tracer.everything_sent()
    assert sent != "[]"  # the exchange is still traced, as metadata
    assert "salary" not in sent and "25 days" not in sent and "Employees get" not in sent
    assert world.apps["Handbook"] in sent


def test_an_operator_can_opt_in_to_chat_content_in_traces(stack, world):
    stack.use_settings(Settings(auth=AuthSettings(mode="jwt"), chat=ChatSettings(trace_content=True)))
    exchange(stack, world, QUESTION)

    sent = stack.tracer.everything_sent()
    assert "salary" in sent and "25 days." in sent


# --------------------------------------------------------------------------- #
# Purge
# --------------------------------------------------------------------------- #
def aged(world, days_old, subject="alice", with_feedback=False):
    """A Conversation whose last message was ``days_old`` days before NOW; returns its id."""
    with world.factory() as session:
        conversation = Conversation(
            owner_subject=subject,
            application_id=world.apps["Handbook"],
            title="t",
            created_at=NOW - timedelta(days=days_old),
            last_activity_at=NOW - timedelta(days=days_old),
        )
        session.add(conversation)
        session.commit()
        message = ConversationMessage(conversation_id=conversation.id, position=1, role="assistant", content="a")
        session.add(message)
        session.commit()
        if with_feedback:
            session.add(MessageFeedback(message_id=message.id, rating="up", shared=True))
            session.commit()
        return str(conversation.id)


def remaining(world):
    with world.factory() as session:
        return {str(c.id) for c in session.scalars(select(Conversation))}


def test_the_purge_removes_conversations_idle_past_retention_and_keeps_the_rest(world):
    old, kept = aged(world, 91), aged(world, 89)

    deleted = purge_expired(opener(world.factory), days=90, now=NOW)

    assert deleted == 1 and remaining(world) == {kept}
    assert old not in remaining(world)


def test_the_boundary_is_exact_a_conversation_exactly_at_the_cutoff_stays(world):
    on_the_line, just_past = aged(world, 90), None
    with world.factory() as session:
        conversation = Conversation(
            owner_subject="x",
            application_id=world.apps["Handbook"],
            title="t",
            last_activity_at=NOW - timedelta(days=90, seconds=1),
        )
        session.add(conversation)
        session.commit()
        just_past = str(conversation.id)

    purge_expired(opener(world.factory), days=90, now=NOW)

    assert remaining(world) == {on_the_line} and just_past not in remaining(world)


def test_the_purge_takes_everything_attached_to_an_expired_conversation(world):
    aged(world, 200, with_feedback=True)

    purge_expired(opener(world.factory), days=90, now=NOW)

    with world.factory() as session:
        assert session.scalars(select(ConversationMessage)).all() == []
        assert session.scalars(select(MessageFeedback)).all() == []


def test_the_purge_is_safe_to_run_repeatedly(world):
    kept = aged(world, 10)
    aged(world, 100)

    assert purge_expired(opener(world.factory), days=90, now=NOW) == 1
    assert purge_expired(opener(world.factory), days=90, now=NOW) == 0
    assert remaining(world) == {kept}


def test_zero_days_keeps_conversations_forever(world):
    ancient = aged(world, 5000)

    assert purge_expired(opener(world.factory), days=0, now=NOW) == 0
    assert remaining(world) == {ancient}


def test_a_purge_that_removed_something_leaves_a_metadata_only_audit_record(world):
    aged(world, 100)
    aged(world, 120)

    purge_expired(opener(world.factory), days=90, now=NOW)

    with world.factory() as session:
        [event] = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "chat.purge")))
    assert event.details["deleted"] == 2 and event.details["days"] == 90
    assert event.auth_method == "system"


def test_a_purge_that_removed_nothing_leaves_no_record(world):
    aged(world, 1)
    purge_expired(opener(world.factory), days=90, now=NOW)
    assert [e for e in events(world.factory) if e.action == "chat.purge"] == []


def test_a_large_backlog_is_purged_in_batches(world):
    for _ in range(7):
        aged(world, 365)

    assert purge_expired(opener(world.factory), days=90, now=NOW, batch_size=3) == 7
    assert remaining(world) == set()


def test_a_dry_run_counts_what_would_go_and_deletes_nothing(world):
    kept = aged(world, 5)
    old = aged(world, 100)

    assert purge_expired(opener(world.factory), days=90, now=NOW, dry_run=True) == 1
    assert remaining(world) == {kept, old}
    assert [e for e in events(world.factory) if e.action == "chat.purge"] == []
