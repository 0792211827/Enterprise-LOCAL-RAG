"""Employee chat history: Conversations and their messages.

Owned by the caller's IdP subject and readable only by them -- no admin surface reads content. New tables only
(ADR-0003); ``application_id`` is a plain column so an Application's removal never reaches into history.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from src.db.interfaces.postgresql import Base
from src.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "chat_conversations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_subject = Column(String, nullable=False, index=True)
    application_id = Column(GUID(), nullable=False, index=True)
    title = Column(String, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Moves only when a message is added; renaming does not extend a Conversation's life (retention counts from here).
    last_activity_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class ConversationMessage(Base):
    __tablename__ = "chat_messages"
    # A turn's place in its Conversation is unique, so an answer can only ever be paired with the question before it.
    __table_args__ = (UniqueConstraint("conversation_id", "position", name="uq_chat_messages_conversation_position"),)

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(GUID(), ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    role = Column(String, nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True)  # assistant messages: the passages the answer drew on
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class MessageFeedback(Base):
    """A thumbs up/down on one assistant message, with an optional comment.

    ``shared`` is the owner's explicit choice to let admins see that one exchange; without it admins see only
    the rating. Removed with the message, and so with its Conversation.
    """

    __tablename__ = "chat_message_feedback"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    message_id = Column(GUID(), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    rating = Column(String, nullable=False)  # "up" or "down"
    comment = Column(Text, nullable=True)
    shared = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
