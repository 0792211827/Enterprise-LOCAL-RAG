"""Employee chat: the Applications an end user may use, Conversations with them, streamed answers with citations,
and the source Documents.

Authorised for the ``querier`` role (see main.py), over the caller's own Grants. Answers come from the shared
application answer module, the same path the playground and ``/v1/chat/completions`` use; this router only
shapes the result for a browser and keeps the history. A Conversation belongs to the IdP subject that started
it and nobody else can reach it -- another caller's Conversation is a 404, exactly like one that never existed.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from src.dependencies import (
    AuditDep,
    EmbeddingsDep,
    LangfuseDep,
    LLMProviderDep,
    OpenSearchDep,
    PrincipalDep,
    ProviderRegistryDep,
    RerankerDep,
    ScopeDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
)
from src.models import AuditAction
from src.models.chat import Conversation
from src.repositories import ApplicationRepository, ConversationRepository, DocumentRepository, FeedbackRepository
from src.schemas.api.chat import (
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
from src.services.applications import is_offered_to, prepare_stream, scope_application
from src.services.audit import AuditContext, QueryAudit, audited_stream
from src.services.audit.events import elapsed_ms
from src.services.chat import history_for_prompt, retrieval_query, trace_chat_exchange
from src.services.providers.llm.registry import ProviderConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

APPLICATION_NOT_FOUND = "Application not found"
CONVERSATION_NOT_FOUND = "Conversation not found"
STREAM_FAILED_MESSAGE = "The answer could not be completed. Please try again."
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
            )
        )
    return ConversationDetail(**_summary(conversation, len(messages)).model_dump(), messages=views)


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
):
    """Ask in a Conversation; the answer streams as server-sent events.

    Events are JSON ``data:`` lines: ``delta`` (``content``), then ``citations`` (``citations``) and ``done``
    (``message_id``, the saved answer's id, or null if it was not saved). A failure mid-stream ends with an
    ``error`` event. The question and the answer are saved together, once the answer is complete.
    """
    repo = ConversationRepository(session)
    conversation = _owned_conversation(repo, conversation_id, principal)
    application = _offered_application(session, conversation.application_id, scope)
    history = history_for_prompt(repo.messages(conversation.id), settings.chat.history_messages)

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
    try:
        prepared = await prepare_stream(
            application,
            payload.query,
            opensearch,
            embeddings,
            provider,
            scope=scope,
            reranker=reranker,
            history=history,
            search_query=retrieval_query(payload.query, history),
        )
    except BaseException as exc:
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
        async for frame in audited_stream(events(), audit, error_frame=_error_frame):
            yield frame
        yield _frame({"type": "done", "message_id": saved["message_id"]})

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
