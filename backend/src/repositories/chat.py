"""Persistence for employee chat: Conversations, their messages and ratings.

Every read or write of a Conversation takes the owner's subject, so "someone else's Conversation" is simply
not found; nothing here lists or reads content across owners. The only cross-owner reads are counts, the
ratings listing (identifiers, never text) and exchanges the owner explicitly shared.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from src.models.chat import Conversation, ConversationMessage, MessageFeedback

DELETE_BATCH = 500


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- the owner's own Conversations ------------------------------------------------------------------------ #
    def create(self, owner_subject: str, application_id: UUID, title: str = "") -> Conversation:
        conversation = Conversation(owner_subject=owner_subject, application_id=application_id, title=title)
        self.session.add(conversation)
        self.session.commit()
        self.session.refresh(conversation)
        return conversation

    def get_owned(self, conversation_id: UUID, owner_subject: str) -> Optional[Conversation]:
        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_subject != owner_subject:
            return None
        return conversation

    def list_owned(self, owner_subject: str) -> List[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.owner_subject == owner_subject)
            .order_by(Conversation.last_activity_at.desc(), Conversation.id)
        )
        return list(self.session.scalars(stmt))

    def rename(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        self.session.commit()
        return conversation

    def messages(self, conversation_id: UUID) -> List[ConversationMessage]:
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.position)
        )
        return list(self.session.scalars(stmt))

    def message_counts(self, conversation_ids: Sequence[UUID]) -> Dict[UUID, int]:
        if not conversation_ids:
            return {}
        stmt = (
            select(ConversationMessage.conversation_id, func.count())
            .where(ConversationMessage.conversation_id.in_(conversation_ids))
            .group_by(ConversationMessage.conversation_id)
        )
        return {conversation_id: count for conversation_id, count in self.session.execute(stmt)}

    def add_exchange(
        self,
        conversation: Conversation,
        question: str,
        answer: str,
        citations: list,
        answer_id: UUID,
        title: Optional[str] = None,
        attempts: int = 3,
    ) -> None:
        """Save a question and its answer together, as the next two turns. Retries if a concurrent send took
        the positions first (the unique position constraint refuses the loser rather than interleave turns)."""
        for attempt in range(attempts):
            last = self.session.scalar(
                select(func.coalesce(func.max(ConversationMessage.position), 0)).where(
                    ConversationMessage.conversation_id == conversation.id
                )
            )
            self.session.add_all(
                [
                    ConversationMessage(conversation_id=conversation.id, position=last + 1, role="user", content=question),
                    ConversationMessage(
                        id=answer_id,
                        conversation_id=conversation.id,
                        position=last + 2,
                        role="assistant",
                        content=answer,
                        citations=citations,
                    ),
                ]
            )
            if title is not None:
                conversation.title = title
            conversation.last_activity_at = datetime.now(timezone.utc)
            try:
                self.session.commit()
                return
            except IntegrityError:
                self.session.rollback()
                if attempt == attempts - 1:
                    raise

    def get_owned_message(self, conversation: Conversation, message_id: UUID) -> Optional[ConversationMessage]:
        message = self.session.get(ConversationMessage, message_id)
        if message is None or message.conversation_id != conversation.id:
            return None
        return message

    # -- removal ---------------------------------------------------------------------------------------------- #
    def delete_many(self, conversation_ids: Sequence[UUID]) -> int:
        """Delete Conversations and everything attached to them, children first; returns how many Conversations went."""
        ids = list(conversation_ids)
        if not ids:
            return 0
        message_ids = select(ConversationMessage.id).where(ConversationMessage.conversation_id.in_(ids))
        self.session.execute(delete(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids)))
        self.session.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id.in_(ids)))
        result = self.session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        self.session.commit()
        return result.rowcount or 0

    def expired_ids(self, cutoff: datetime, limit: int = DELETE_BATCH) -> List[UUID]:
        """Conversations whose last activity is strictly before ``cutoff``."""
        stmt = select(Conversation.id).where(Conversation.last_activity_at < cutoff).limit(limit)
        return list(self.session.scalars(stmt))

    def count_expired(self, cutoff: datetime) -> int:
        return (
            self.session.scalar(select(func.count()).select_from(Conversation).where(Conversation.last_activity_at < cutoff)) or 0
        )

    # -- counts only, for admins ------------------------------------------------------------------------------ #
    def counts_by_application(self) -> List[dict]:
        conversations = dict(
            self.session.execute(select(Conversation.application_id, func.count()).group_by(Conversation.application_id)).all()
        )
        messages = dict(
            self.session.execute(
                select(Conversation.application_id, func.count(ConversationMessage.id))
                .join(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
                .group_by(Conversation.application_id)
            ).all()
        )
        return [
            {"application_id": application_id, "conversations": count, "messages": messages.get(application_id, 0)}
            for application_id, count in conversations.items()
        ]


class FeedbackRepository:
    """Ratings. Written only through the message's owner; read by admins as identifiers and, for shared exchanges, text."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, message_id: UUID) -> Optional[MessageFeedback]:
        return self.session.scalar(select(MessageFeedback).where(MessageFeedback.message_id == message_id))

    def for_messages(self, message_ids: Sequence[UUID]) -> Dict[UUID, MessageFeedback]:
        if not message_ids:
            return {}
        stmt = select(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids))
        return {feedback.message_id: feedback for feedback in self.session.scalars(stmt)}

    def upsert(self, message_id: UUID, rating: str, comment: Optional[str], shared: bool) -> MessageFeedback:
        feedback = self.get(message_id)
        if feedback is None:
            feedback = MessageFeedback(message_id=message_id)
            self.session.add(feedback)
        feedback.rating, feedback.comment, feedback.shared = rating, comment, shared
        feedback.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return feedback

    def clear(self, message_id: UUID) -> None:
        self.session.execute(delete(MessageFeedback).where(MessageFeedback.message_id == message_id))
        self.session.commit()

    # -- the admin views: identifiers and ratings; text only for exchanges shared on purpose ------------------- #
    def ratings(self, application_id: Optional[UUID] = None) -> List[dict]:
        """Every rating with its message and application ids. Never any message text."""
        stmt = (
            select(MessageFeedback, Conversation.application_id)
            .join(ConversationMessage, ConversationMessage.id == MessageFeedback.message_id)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .order_by(MessageFeedback.updated_at.desc(), MessageFeedback.id)
        )
        if application_id is not None:
            stmt = stmt.where(Conversation.application_id == application_id)
        return [{"feedback": feedback, "application_id": app_id} for feedback, app_id in self.session.execute(stmt)]

    def summary(self) -> List[dict]:
        stmt = (
            select(Conversation.application_id, MessageFeedback.rating, func.count())
            .select_from(MessageFeedback)
            .join(ConversationMessage, ConversationMessage.id == MessageFeedback.message_id)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .group_by(Conversation.application_id, MessageFeedback.rating)
        )
        totals: Dict[UUID, Dict[str, int]] = {}
        for app_id, rating, count in self.session.execute(stmt):
            totals.setdefault(app_id, {"up": 0, "down": 0})[rating] = count
        return [{"application_id": app_id, **counts} for app_id, counts in totals.items()]

    def shared_exchanges(self, application_id: Optional[UUID] = None) -> List[dict]:
        """The exchanges whose owners chose to share them: question, answer and rating. Nothing else is returned."""
        stmt = (
            select(MessageFeedback, ConversationMessage, Conversation.application_id)
            .join(ConversationMessage, ConversationMessage.id == MessageFeedback.message_id)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(MessageFeedback.shared.is_(True))
            .order_by(MessageFeedback.updated_at.desc(), MessageFeedback.id)
        )
        if application_id is not None:
            stmt = stmt.where(Conversation.application_id == application_id)
        exchanges = []
        for feedback, answer, app_id in self.session.execute(stmt):
            question = self.session.scalar(
                select(ConversationMessage.content).where(
                    ConversationMessage.conversation_id == answer.conversation_id,
                    ConversationMessage.position == answer.position - 1,
                    ConversationMessage.role == "user",
                )
            )
            exchanges.append({"feedback": feedback, "answer": answer, "question": question or "", "application_id": app_id})
        return exchanges
