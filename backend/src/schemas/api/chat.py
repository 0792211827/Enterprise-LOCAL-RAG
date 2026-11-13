"""Request and response schemas for the employee chat surface."""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatApplication(BaseModel):
    """An Application as an end user sees it: no prompt, no knowledge bases, no endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    citations_enabled: bool


class ChatMessageRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)


class ChatCitation(BaseModel):
    """A passage an answer drew on; ``document_id`` opens the source Document."""

    index: int
    document_id: Optional[str] = None
    title: str = ""
    section_title: Optional[str] = None
    chunk_id: Optional[str] = None
    passage: str = ""
    score: float = 0.0


class ChatDocument(BaseModel):
    """A source Document as the chat UI opens it from a citation."""

    id: str
    title: str
    source_uri: Optional[str] = None
    content_type: Optional[str] = None
    text: str = ""


class ConversationCreate(BaseModel):
    application_id: UUID
    title: Optional[str] = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ConversationSummary(BaseModel):
    id: str
    application_id: str
    title: str
    created_at: datetime
    last_activity_at: datetime
    message_count: int = 0


class MessageFeedbackInput(BaseModel):
    rating: Literal["up", "down"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    share_with_admins: bool = False


class MessageFeedbackView(BaseModel):
    rating: Literal["up", "down"]
    comment: Optional[str] = None
    share_with_admins: bool = False


class ChatMessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: List[ChatCitation] = Field(default_factory=list)
    created_at: datetime
    feedback: Optional[MessageFeedbackView] = None


class ConversationDetail(ConversationSummary):
    messages: List[ChatMessageView] = Field(default_factory=list)


class ApplicationChatCounts(BaseModel):
    application_id: str
    application_name: str
    conversations: int
    messages: int


class ConversationCounts(BaseModel):
    conversations: int
    messages: int
    by_application: List[ApplicationChatCounts]


class ApplicationRatings(BaseModel):
    application_id: str
    application_name: str
    up: int
    down: int


class RatingView(BaseModel):
    """A rating, identified by message. The comment is the user's own feedback to admins; no Conversation text."""

    message_id: str
    application_id: str
    rating: str
    comment: Optional[str] = None
    shared: bool
    rated_at: str


class SharedExchange(BaseModel):
    message_id: str
    application_id: str
    rating: str
    comment: Optional[str] = None
    question: str
    answer: str
    sources: List[str]
