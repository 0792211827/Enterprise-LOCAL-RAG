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


class ChatConfig(BaseModel):
    """What the chat UI needs to know about this deployment."""

    images_enabled: bool
    image_max_mb: int
    attachments_per_message: int


class ChatMessageRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)


class ChatCitation(BaseModel):
    """A passage an answer drew on; ``document_id`` opens the source Document."""

    index: int
    document_id: Optional[str] = None
    title: str = ""
    section_title: Optional[str] = None
    # Where the passage sits in its source (page, slide, sheet and rows); ``locator_text`` is that in words, e.g. "slide 7".
    locator: Optional[dict] = None
    locator_text: Optional[str] = None
    source_url: Optional[str] = None  # the page of a URL Document, which the Citation links to
    chunk_id: Optional[str] = None
    passage: str = ""
    score: float = 0.0
    # Set for a passage of an Attachment: it identifies the Attachment (``title`` is its file name), not a Document.
    attachment_id: Optional[str] = None


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


class AttachmentView(BaseModel):
    """A document attached to a Conversation. ``attached_to`` is empty until the next message is sent."""

    id: str
    filename: str
    kind: str
    size_bytes: int
    token_estimate: int
    # True for a document too large for the prompt: it is searched passage by passage, not sent in full.
    searched: bool = False
    attached_to: Optional[str] = None
    created_at: datetime


class ChatMessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: List[ChatCitation] = Field(default_factory=list)
    created_at: datetime
    attachments: List[AttachmentView] = Field(default_factory=list)


class ConversationDetail(ConversationSummary):
    messages: List[ChatMessageView] = Field(default_factory=list)
    # Attached but not yet sent with a message.
    pending_attachments: List[AttachmentView] = Field(default_factory=list)


class ApplicationChatCounts(BaseModel):
    application_id: str
    application_name: str
    conversations: int
    messages: int


class ConversationCounts(BaseModel):
    conversations: int
    messages: int
    by_application: List[ApplicationChatCounts]
