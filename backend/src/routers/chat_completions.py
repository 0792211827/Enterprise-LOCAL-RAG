"""OpenAI-compatible chat completions.

Mounted at bare ``/v1`` (not ``/api/v1``) because OpenAI SDK clients append
``/chat/completions`` to a ``base_url`` that ends in ``/v1``. That makes the
platform a drop-in target for the stock ``openai`` package:

    client = OpenAI(base_url="http://localhost:8000/v1", api_key="sk-rag-...")
    client.chat.completions.create(model="<application-slug>", messages=[...])

Two behaviours to be explicit about, both surfaced in the admin UI:

* **API keys are issued but NOT enforced.** A bearer token is parsed and, if it
  matches a known key, its ``last_used_at`` is stamped -- but requests carrying
  an unknown token, or no token at all, are still served. Treat this endpoint as
  unauthenticated and do not expose it outside a trusted network.
* **Conversation history is ignored.** Retrieval and generation are driven by the
  last ``user`` message only; earlier turns are not carried into the prompt.
"""

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.dependencies import EmbeddingsDep, LLMProviderDep, OpenSearchDep, SessionDep
from src.repositories import ApiKeyRepository, ApplicationRepository
from src.schemas.api.domain import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
)
from src.services.applications import (
    NO_KNOWLEDGE_BASE_MESSAGE,
    answer_for_application,
    effective_system_prompt,
    retrieve_for_application,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai-compatible"])


def _openai_error(status_code: int, message: str, code: str, err_type: str = "invalid_request_error"):
    """Return an OpenAI-shaped error body.

    FastAPI's default ``{"detail": ...}`` surfaces as an opaque error in the
    OpenAI SDKs, which makes a misconfigured integration hard to diagnose.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "param": None, "code": code}},
    )


def _record_key_usage(session: Any, authorization: Optional[str]) -> None:
    """Stamp last_used_at when a recognised key is presented.

    Deliberately does not reject unknown or absent keys -- see the module
    docstring. Any failure here is swallowed: observability must never break a
    request on an endpoint that does not authenticate.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return
    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        return
    try:
        repo = ApiKeyRepository(session)
        key = repo.get_by_raw_key(raw)
        if key is not None and key.revoked_at is None:
            repo.touch(key)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not record API key usage", exc_info=True)


@router.post("/chat/completions")
async def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    session: SessionDep,
    opensearch: OpenSearchDep,
    embeddings: EmbeddingsDep,
    llm: LLMProviderDep,
    authorization: Optional[str] = Header(default=None),
):
    """Answer via a RAG application, addressed by its slug as ``model``."""
    application = ApplicationRepository(session).get_by_slug(payload.model)
    if application is None:
        return _openai_error(
            404,
            f"Model '{payload.model}' not found. Use a RAG application slug as the model name.",
            code="model_not_found",
        )

    _record_key_usage(session, authorization)

    user_messages = [m for m in payload.messages if m.role == "user"]
    if not user_messages:
        return _openai_error(400, "At least one message with role 'user' is required.", code="missing_user_message")
    query = user_messages[-1].content

    # A leading system message augments, rather than replaces, the application's
    # configured prompt. Passed through explicitly -- assigning to the ORM object
    # would risk a later commit persisting a caller-supplied prompt.
    system_override = next((m.content for m in payload.messages if m.role == "system"), None)

    if payload.stream:
        if not application.streaming_enabled:
            return _openai_error(
                400,
                f"Streaming is disabled for application '{application.slug}'.",
                code="streaming_disabled",
            )
        # Retrieval happens here, not inside the generator: a StreamingResponse
        # body runs after the request handler returns, by which point the
        # request-scoped session is closed and any lazy load on the application
        # (e.g. knowledge_bases) would raise. Everything the generator needs is
        # resolved to plain values first.
        chunks, _hits, _mode, model = await retrieve_for_application(application, query, opensearch, embeddings)
        return StreamingResponse(
            _stream_completion(
                slug=application.slug,
                model=model,
                system_prompt=effective_system_prompt(application, system_override),
                chunks=chunks,
                query=query,
                llm=llm,
            ),
            media_type="text/event-stream",
        )

    result = await answer_for_application(
        application=application,
        query=query,
        opensearch=opensearch,
        embeddings=embeddings,
        llm=llm,
        system_prompt_override=system_override,
    )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=application.slug,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=result.answer),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(),
    )


async def _stream_completion(slug, model, system_prompt, chunks, query, llm):
    """Emit an SSE stream in the OpenAI chat.completion.chunk format.

    Takes only plain values -- no ORM objects -- because this runs after the
    request-scoped database session has closed.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def envelope(delta: dict, finish_reason: Optional[str] = None) -> str:
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": slug,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body)}\n\n"

    try:
        # The opening chunk carries the role, matching OpenAI's wire format.
        yield envelope({"role": "assistant"})

        if chunks is None:
            yield envelope({"content": NO_KNOWLEDGE_BASE_MESSAGE})
        else:
            async for chunk in llm.generate_rag_answer_stream(
                query=query, chunks=chunks, model=model, system_prompt=system_prompt
            ):
                delta = chunk.get("response", "") if isinstance(chunk, dict) else str(chunk)
                if delta:
                    yield envelope({"content": delta})
        yield envelope({}, finish_reason="stop")
    except Exception as exc:  # noqa: BLE001 - stream must terminate cleanly
        logger.error("Streaming completion failed: %s", exc)
        yield envelope({"content": f"\n[error: {exc}]"}, finish_reason="stop")

    yield "data: [DONE]\n\n"
