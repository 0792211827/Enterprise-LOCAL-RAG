"""Employee chat: the Applications an end user may use, Conversations with them, streamed answers with citations,
and the source Documents.

Authorised for the ``querier`` role (see main.py), over the caller's own Grants. Answers come from the shared
application answer module, the same path the playground and ``/v1/chat/completions`` use; this router only
shapes the result for a browser and keeps the history. A Conversation belongs to the IdP subject that started
it and nobody else can reach it -- another caller's Conversation is a 404, exactly like one that never existed.
"""

import dataclasses
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from src.dependencies import (
    AuditDep,
    ChatLimiterDep,
    EmbeddingsDep,
    LangfuseDep,
    LLMProviderDep,
    OpenSearchDep,
    PDFParserDep,
    PrincipalDep,
    ProviderRegistryDep,
    RerankerDep,
    ScopeDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
)
from src.models import AuditAction
from src.models.chat import Conversation, ConversationAttachment
from src.repositories import (
    ApplicationRepository,
    AttachmentRepository,
    ConversationRepository,
    DocumentRepository,
    FeedbackRepository,
)
from src.schemas.api.chat import (
    AttachmentView,
    ChatApplication,
    ChatCitation,
    ChatDocument,
    ChatMessageRequest,
    ChatMessageView,
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    MessageFeedbackInput,
    MessageFeedbackView,
)
from src.services.applications import is_agentic, is_offered_to, prepare_stream, scope_application
from src.services.audit import AuditContext, QueryAudit, audited_stream
from src.services.audit.events import elapsed_ms, record_event
from src.services.chat import (
    AttachmentError,
    AttachmentText,
    RateLimited,
    estimate_tokens,
    extract_text,
    history_for_prompt,
    question_with_attachments,
    retrieval_query,
    sniff_kind,
    trace_chat_exchange,
)
from src.services.ingestion.uploads import display_name, read_capped
from src.services.providers.llm.registry import ProviderConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

APPLICATION_NOT_FOUND = "Application not found"
CONVERSATION_NOT_FOUND = "Conversation not found"
STREAM_FAILED_MESSAGE = "The answer could not be completed. Please try again."
RATE_LIMIT_MESSAGES = {
    "rate": "You are sending messages too quickly. Please wait {seconds} seconds and try again.",
    "concurrency": "You already have answers being written. Please wait for one to finish and try again.",
}
TITLE_LENGTH = 80


def _frame(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _error_frame(message: str) -> str:
    return _frame({"type": "error", "message": message})


def _citations(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One citation per retrieved passage, in rank order."""
    return [
        ChatCitation(
            index=index,
            document_id=str(hit["document_id"]) if hit.get("document_id") else None,
            title=hit.get("title") or "",
            section_title=hit.get("section_title"),
            chunk_id=hit.get("chunk_id"),
            passage=hit.get("chunk_text") or "",
            score=float(hit.get("score") or 0.0),
        ).model_dump()
        for index, hit in enumerate(hits, 1)
    ]


def _title_from(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"


def _summary(conversation: Conversation, message_count: int) -> ConversationSummary:
    return ConversationSummary(
        id=str(conversation.id),
        application_id=str(conversation.application_id),
        title=conversation.title,
        created_at=conversation.created_at,
        last_activity_at=conversation.last_activity_at,
        message_count=message_count,
    )


def _feedback_view(feedback) -> MessageFeedbackView:
    return MessageFeedbackView(rating=feedback.rating, comment=feedback.comment, share_with_admins=feedback.shared)


def _attachment_view(attachment: ConversationAttachment) -> AttachmentView:
    return AttachmentView(
        id=str(attachment.id),
        filename=attachment.filename,
        kind=attachment.kind,
        size_bytes=attachment.size_bytes,
        token_estimate=attachment.token_estimate,
        attached_to=str(attachment.message_id) if attachment.message_id else None,
        created_at=attachment.created_at,
    )


def _owned_conversation(repo: ConversationRepository, conversation_id: UUID, principal) -> Conversation:
    conversation = repo.get_owned(conversation_id, principal.subject)
    if conversation is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND)
    return conversation


@router.get("/applications", response_model=List[ChatApplication])
def list_applications(session: SessionDep, scope: ScopeDep):
    """The Applications this caller may use: those whose every Knowledge Base they hold a Grant on."""
    applications = ApplicationRepository(session).get_all(limit=1000)
    return [application for application in applications if is_offered_to(application, scope)]


def _offered_application(session, application_id: UUID, scope):
    application = ApplicationRepository(session).get_by_id(application_id)
    # An Application that is not offered looks exactly like one that does not exist.
    if application is None or not is_offered_to(application, scope):
        raise HTTPException(status_code=404, detail=APPLICATION_NOT_FOUND)
    return application


@router.post("/conversations", response_model=ConversationSummary, status_code=201)
def create_conversation(payload: ConversationCreate, principal: PrincipalDep, session: SessionDep, scope: ScopeDep):
    """Start a Conversation with an Application the caller is offered."""
    application = _offered_application(session, payload.application_id, scope)
    conversation = ConversationRepository(session).create(principal.subject, application.id, (payload.title or "").strip())
    return _summary(conversation, 0)


@router.get("/conversations", response_model=List[ConversationSummary])
def list_conversations(principal: PrincipalDep, session: SessionDep):
    """The caller's own Conversations, most recently active first."""
    repo = ConversationRepository(session)
    conversations = repo.list_owned(principal.subject)
    counts = repo.message_counts([c.id for c in conversations])
    return [_summary(c, counts.get(c.id, 0)) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def open_conversation(conversation_id: UUID, principal: PrincipalDep, session: SessionDep):
    repo = ConversationRepository(session)
    conversation = _owned_conversation(repo, conversation_id, principal)
    messages = repo.messages(conversation.id)
    feedback = FeedbackRepository(session).for_messages([m.id for m in messages])
    attachments = AttachmentRepository(session)
    by_message = attachments.for_messages([m.id for m in messages])
    pending = [a for a in attachments.list(conversation.id) if a.message_id is None]
    views = []
    for message in messages:
        rating = feedback.get(message.id)
        views.append(
            ChatMessageView(
                id=str(message.id),
                role=message.role,
                content=message.content,
                citations=message.citations or [],
                created_at=message.created_at,
                feedback=_feedback_view(rating) if rating else None,
                attachments=[_attachment_view(a) for a in by_message.get(message.id, [])],
            )
        )
    return ConversationDetail(
        **_summary(conversation, len(messages)).model_dump(),
        messages=views,
        pending_attachments=[_attachment_view(a) for a in pending],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: UUID, payload: ConversationRename, principal: PrincipalDep, session: SessionDep):
    repo = ConversationRepository(session)
    conversation = repo.rename(_owned_conversation(repo, conversation_id, principal), payload.title.strip())
    return _summary(conversation, repo.message_counts([conversation.id]).get(conversation.id, 0))


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, principal: PrincipalDep, session: SessionDep):
    repo = ConversationRepository(session)
    repo.delete_many([_owned_conversation(repo, conversation_id, principal).id])
    return Response(status_code=204)


def _record_attachment_event(audit_recorder, request, principal, application_id, outcome: str, **details) -> None:
    """Attachment events carry metadata only: ids, type, size and a digest of the content -- never the file's name or text."""
    context = dataclasses.replace(AuditContext.capture(request, principal), application_id=application_id)
    record_event(audit_recorder, context, action=AuditAction.CHAT_ATTACHMENT.value, outcome=outcome, details=details)


@router.post("/conversations/{conversation_id}/attachments", response_model=AttachmentView, status_code=201)
async def upload_attachment(
    conversation_id: UUID,
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    parser: PDFParserDep,
    audit_recorder: AuditDep,
    file: UploadFile = File(...),
):
    """Attach a document to a Conversation, to ask about it.

    The type is checked from the content, the text is extracted with the platform's parser, and the file itself is
    not kept. All of a Conversation's Attachments go into the prompt in full, so together they must fit the token
    budget. Only the Conversation's owner can attach, list or remove them.
    """
    repo = ConversationRepository(session)
    conversation = _owned_conversation(repo, conversation_id, principal)
    application = _offered_application(session, conversation.application_id, scope)
    if is_agentic(application):
        raise HTTPException(status_code=409, detail="This assistant does not take attachments.")
    chat = settings.chat
    attachments = AttachmentRepository(session)
    existing = attachments.list(conversation.id)
    attachment_id = uuid4()
    size, raw = 0, b""

    def refuse(error: AttachmentError):
        _record_attachment_event(
            audit_recorder, request, principal, application.id, "error",
            event="upload", conversation_id=str(conversation_id), rejected=error.code, size_bytes=size,
        )  # fmt: skip
        raise HTTPException(status_code=error.status_code, detail=error.message)

    pending = sum(1 for a in existing if a.message_id is None)
    if pending >= chat.attachments_per_message:
        refuse(
            AttachmentError(
                422,
                "too_many",
                f"At most {chat.attachments_per_message} documents can be attached to one message. Send this message or remove one.",
            )
        )
    try:
        raw = await read_capped(file, chat.attachment_max_mb, "CHAT__ATTACHMENT_MAX_MB")
    except HTTPException as exc:
        refuse(AttachmentError(exc.status_code, "too_large", str(exc.detail)))
    size = len(raw)
    limit_bytes = chat.conversation_attachments_max_mb * 1024 * 1024
    if sum(a.size_bytes for a in existing) + size > limit_bytes:
        refuse(
            AttachmentError(
                413,
                "conversation_full",
                f"A conversation can hold {chat.conversation_attachments_max_mb} MB of documents. Remove one to attach this file.",
            )
        )
    name = display_name(file.filename)
    try:
        kind = sniff_kind(file.filename, raw)
        text = await extract_text(kind, raw, parser)
        tokens = estimate_tokens(text)
        used = sum(a.token_estimate for a in existing)
        if used + tokens > chat.attachment_token_budget:
            raise AttachmentError(
                422,
                "over_budget",
                f"This document is too long to include in full: it would bring the attached text to about {used + tokens:,} "
                f"tokens, over the {chat.attachment_token_budget:,} allowed. Attach a shorter file or remove another one.",
            )
    except AttachmentError as error:
        refuse(error)

    _record_attachment_event(
        audit_recorder, request, principal, application.id, "ok",
        event="upload", conversation_id=str(conversation_id), attachment_id=str(attachment_id),
        kind=kind, size_bytes=size, tokens=tokens, sha256=hashlib.sha256(raw).hexdigest(),
    )  # fmt: skip
    created = attachments.add(
        ConversationAttachment(
            id=attachment_id,
            conversation_id=conversation.id,
            owner_subject=principal.subject,
            filename=name,
            kind=kind,
            size_bytes=size,
            token_estimate=tokens,
            text=text,
        )
    )
    return _attachment_view(created)


@router.get("/conversations/{conversation_id}/attachments", response_model=List[AttachmentView])
def list_attachments(conversation_id: UUID, principal: PrincipalDep, session: SessionDep):
    conversation = _owned_conversation(ConversationRepository(session), conversation_id, principal)
    return [_attachment_view(a) for a in AttachmentRepository(session).list(conversation.id)]


@router.delete("/conversations/{conversation_id}/attachments/{attachment_id}", status_code=204)
def remove_attachment(
    conversation_id: UUID,
    attachment_id: UUID,
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    audit_recorder: AuditDep,
):
    """Remove a document and its extracted text; later questions are answered without it."""
    conversation = _owned_conversation(ConversationRepository(session), conversation_id, principal)
    attachments = AttachmentRepository(session)
    attachment = attachments.get(conversation.id, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _record_attachment_event(
        audit_recorder, request, principal, conversation.application_id, "ok",
        event="remove", conversation_id=str(conversation.id), attachment_id=str(attachment.id),
        kind=attachment.kind, size_bytes=attachment.size_bytes,
    )  # fmt: skip
    attachments.remove(attachment)
    return Response(status_code=204)


def _rated_message(repo: ConversationRepository, conversation_id: UUID, message_id: UUID, principal):
    """The caller's own answer to rate; anything else -- someone else's, another conversation's, unknown -- is a 404."""
    conversation = _owned_conversation(repo, conversation_id, principal)
    message = repo.get_owned_message(conversation, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@router.put("/conversations/{conversation_id}/messages/{message_id}/feedback", response_model=MessageFeedbackView)
def rate_message(
    conversation_id: UUID, message_id: UUID, payload: MessageFeedbackInput, principal: PrincipalDep, session: SessionDep
):
    """Rate one answer. ``share_with_admins`` lets admins see that one exchange (question and answer); without
    it they see only that a rating exists."""
    message = _rated_message(ConversationRepository(session), conversation_id, message_id, principal)
    if message.role != "assistant":
        raise HTTPException(status_code=400, detail="Only answers can be rated")
    comment = (payload.comment or "").strip() or None
    feedback = FeedbackRepository(session).upsert(message.id, payload.rating, comment, payload.share_with_admins)
    return _feedback_view(feedback)


@router.delete("/conversations/{conversation_id}/messages/{message_id}/feedback", status_code=204)
def withdraw_rating(conversation_id: UUID, message_id: UUID, principal: PrincipalDep, session: SessionDep):
    message = _rated_message(ConversationRepository(session), conversation_id, message_id, principal)
    FeedbackRepository(session).clear(message.id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    payload: ChatMessageRequest,
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    open_session: SessionOpenerDep,
    scope: ScopeDep,
    settings: SettingsDep,
    opensearch: OpenSearchDep,
    embeddings: EmbeddingsDep,
    llm: LLMProviderDep,
    providers: ProviderRegistryDep,
    reranker: RerankerDep,
    audit_recorder: AuditDep,
    langfuse: LangfuseDep,
    limiter: ChatLimiterDep,
):
    """Ask in a Conversation; the answer streams as server-sent events.

    Events are JSON ``data:`` lines: ``delta`` (``content``), then ``citations`` (``citations``) and ``done``
    (``message_id``, the saved answer's id, or null if it was not saved). A failure mid-stream ends with an
    ``error`` event. The question and the answer are saved together, once the answer is complete.

    Each user may send a limited number of messages a minute and have a limited number of answers being written at
    once; beyond that this is a 429 with a ``Retry-After`` header.
    """
    repo = ConversationRepository(session)
    conversation = _owned_conversation(repo, conversation_id, principal)
    application = _offered_application(session, conversation.application_id, scope)
    history = history_for_prompt(repo.messages(conversation.id), settings.chat.history_messages)
    attached = AttachmentRepository(session).list(conversation.id)
    if attached and is_agentic(application):
        raise HTTPException(status_code=409, detail="This assistant does not take attachments. Remove them to continue.")
    if sum(a.token_estimate for a in attached) > settings.chat.attachment_token_budget:
        raise HTTPException(
            status_code=422, detail="The attached documents are too long to include in full. Remove one to continue."
        )
    pending_attachments = [a.id for a in attached if a.message_id is None]
    model_query = question_with_attachments(payload.query, [AttachmentText(a.filename, a.text) for a in attached])

    try:
        provider = providers.for_application(application, default=llm)
    except ProviderConfigurationError as exc:
        logger.error("Chat application %s is misconfigured: %s", application.slug, exc)
        raise HTTPException(status_code=500, detail="This assistant is not available right now.")

    # Everything the generator needs is resolved to plain values: it runs after the request session closes.
    citations_enabled = application.citations_enabled
    owner = principal.subject
    first_question = not conversation.title
    audit = QueryAudit(
        audit_recorder,
        AuditContext.capture(request, principal),
        action=AuditAction.QUERY_ANSWER.value,
        query=payload.query,
        decision=scope_application(application, scope),
        application_id=application.id,
        redact_text=True,  # chat text never enters the audit log; the digests still do
    )
    slot = None
    if limiter is not None:
        try:
            slot = await limiter.admit(principal.subject)
        except RateLimited as limited:
            raise HTTPException(
                status_code=429,
                detail=RATE_LIMIT_MESSAGES[limited.reason].format(seconds=limited.retry_after),
                headers={"Retry-After": str(limited.retry_after)},
            )
    try:
        prepared = await prepare_stream(
            application,
            model_query,
            opensearch,
            embeddings,
            provider,
            scope=scope,
            reranker=reranker,
            history=history,
            # Retrieval sees the question only: attached text is for the model, not for searching the knowledge bases.
            search_query=retrieval_query(payload.query, history) or payload.query,
        )
    except BaseException as exc:
        if slot is not None:
            slot.release()
        audit.record_after_abort(exc)
        raise
    audit.hits, audit.model = prepared.hits, prepared.model
    audit.details = {**(prepared.details or {}), "conversation_id": str(conversation_id)}
    audit.note_rerank(prepared.rerank)

    saved: Dict[str, Optional[str]] = {"message_id": None}
    started = time.perf_counter()

    def save_exchange(answer: str, citations: List[Dict[str, Any]]) -> None:
        with open_session() as own_session:
            own_repo = ConversationRepository(own_session)
            current = own_repo.get_owned(conversation_id, owner)
            if current is None:  # deleted while the answer was being generated
                return
            message_id = uuid4()
            own_repo.add_exchange(
                current,
                payload.query,
                answer,
                citations,
                message_id,
                title=_title_from(payload.query) if first_question else None,
                attachment_ids=pending_attachments,
            )
            saved["message_id"] = str(message_id)

    async def events():
        answer: List[str] = []
        try:
            async for delta in prepared.deltas():
                answer.append(delta)
                yield _frame({"type": "delta", "content": delta})
            citations = _citations(prepared.hits) if citations_enabled else []
            yield _frame({"type": "citations", "citations": citations})
            save_exchange("".join(answer), citations)
        except Exception as exc:  # noqa: BLE001 - the stream must end cleanly
            # The type only: a provider's error text can echo the prompt, and chat text stays out of logs.
            logger.error("Chat stream failed: %s", type(exc).__name__)
            audit.fail(exc)
            yield _error_frame(STREAM_FAILED_MESSAGE)
        finally:
            audit.answer = "".join(answer)
            _trace(audit, application.id, "".join(answer))

    def _trace(audit, application_id, answer_text):
        metadata = {
            "request_id": audit.context.request_id,
            "application_id": str(application_id),
            "conversation_id": str(conversation_id),
            "model": audit.model,
            "retrieved": len(audit.hits or []),
            "outcome": audit.outcome or "ok",
            "latency_ms": elapsed_ms(started),
            **{k: v for k, v in (audit.details or {}).items() if k in ("reranked", "rerank_skipped", "agentic")},
        }
        content = settings.chat.trace_content
        trace_chat_exchange(
            langfuse, metadata, question=payload.query if content else None, answer=answer_text if content else None
        )

    async def body():
        # The slot is freed however the stream ends: finished, failed, or the client went away.
        try:
            async for frame in audited_stream(events(), audit, error_frame=_error_frame):
                yield frame
            yield _frame({"type": "done", "message_id": saved["message_id"]})
        finally:
            if slot is not None:
                slot.release()

    return StreamingResponse(body(), media_type="text/event-stream")


@router.get("/documents/{document_id}", response_model=ChatDocument)
def open_document(document_id: UUID, session: SessionDep, scope: ScopeDep):
    """The source Document behind a citation, if the caller may read its Knowledge Base."""
    document = DocumentRepository(session).get_by_id(document_id)
    if document is None or not scope.restrict([str(document.knowledge_base_id)]).permitted:
        raise HTTPException(status_code=404, detail="Document not found")
    return ChatDocument(
        id=str(document.id),
        title=document.title,
        source_uri=document.source_uri,
        content_type=document.content_type,
        text=document.raw_text or "",
    )
