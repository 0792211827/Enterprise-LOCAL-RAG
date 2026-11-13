"""Shared answer path for a configured RAG application.

Both the admin playground (``POST /api/v1/applications/{id}/ask``) and the
OpenAI-compatible endpoint (``POST /v1/chat/completions``) route through here so
the two can never drift apart -- what the playground proves is exactly what a
copy-pasted snippet will do.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.models.application import RAGApplication
from src.models.enums import RAGStrategy, RetrievalMode
from src.services.access import RetrievalScope, ScopeDecision
from src.services.agents.factory import make_agentic_rag_service
from src.services.retrieval import retrieve

NO_KNOWLEDGE_BASE_MESSAGE = (
    "This assistant has no knowledge bases attached yet, so there is nothing to "
    "answer from. Attach a knowledge base with at least one ingested document."
)

NO_PERMITTED_KNOWLEDGE_MESSAGE = (
    "You don't have access to any of the knowledge bases this assistant answers "
    "from, so there is nothing to answer from. Ask an administrator for access."
)


def scope_application(application: RAGApplication, scope: Optional[RetrievalScope] = None) -> ScopeDecision:
    """The application's knowledge bases, narrowed to what the caller may read.

    ``scope=None`` means no permission filter (the pre-RBAC behaviour).
    """
    kb_ids = [str(kb.id) for kb in (application.knowledge_bases or [])]
    return (scope or RetrievalScope.unrestricted("no scope supplied")).restrict(kb_ids)


def blocked_message(application: RAGApplication) -> str:
    """Why retrieval returned nothing to answer from (``chunks is None``)."""
    if not application.knowledge_bases:
        return NO_KNOWLEDGE_BASE_MESSAGE
    return NO_PERMITTED_KNOWLEDGE_MESSAGE


def effective_system_prompt(application: RAGApplication, override: Optional[str] = None) -> Optional[str]:
    """Compose the system prompt without mutating the ORM object.

    Assigning to ``application.system_prompt`` would mark the row dirty, and any
    later commit in the same session would silently persist a caller-supplied
    prompt.
    """
    base = application.system_prompt
    if override and base:
        return f"{base}\n\n{override}"
    return override or base


@dataclass
class ApplicationRetrieval:
    """What retrieval returned for an application. ``chunks is None`` means nothing was searchable."""

    chunks: Optional[List[Dict[str, Any]]]
    hits: List[Dict[str, Any]]
    mode: str
    model: str
    rerank: Optional[str] = None  # see RetrievalResult.rerank


@dataclass
class ApplicationAnswer:
    answer: str
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    hits: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = RetrievalMode.HYBRID.value
    scope_decision: Optional[ScopeDecision] = None
    model: Optional[str] = None
    # Strategy-specific facts for the audit record (None for a traditional answer).
    details: Optional[Dict[str, Any]] = None
    rerank: Optional[str] = None  # see RetrievalResult.rerank


def is_agentic(application: RAGApplication) -> bool:
    return getattr(application, "rag_strategy", None) == RAGStrategy.AGENTIC.value


def _chunk_from_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "chunk_text": hit.get("chunk_text", ""),
        "title": hit.get("title", ""),
        "score": hit.get("score", 0.0),
        "document_id": hit.get("document_id"),
        "section_title": hit.get("section_title"),
    }


async def retrieve_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    top_k_override: int | None = None,
    scope: Optional[RetrievalScope] = None,
    reranker: Any = None,
) -> tuple[list, list, str, str]:
    """Retrieve chunks scoped to the application's knowledge bases.

    :param scope: the caller's retrieval permissions; ``None`` applies none.
    :returns: ``(chunks, hits, mode, model)``. ``chunks is None`` means there
        was nothing the caller may search -- no knowledge bases attached, or
        none permitted -- and nothing was retrieved; see ``blocked_message``.
    """
    found = await retrieve_application(application, query, opensearch, embeddings, top_k_override, scope, reranker)
    return found.chunks, found.hits, found.mode, found.model


async def retrieve_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    top_k_override: int | None = None,
    scope: Optional[RetrievalScope] = None,
    reranker: Any = None,
) -> ApplicationRetrieval:
    """``retrieve_for_application``, keeping whether reranking was applied or skipped."""
    retrieval_cfg = application.retrieval_configuration
    model_cfg = application.model_configuration
    mode = retrieval_cfg.mode if retrieval_cfg else RetrievalMode.HYBRID.value
    top_k = top_k_override or (retrieval_cfg.top_k if retrieval_cfg else 8)
    model = model_cfg.llm_model if model_cfg else "llama3.2:1b"
    min_score = (retrieval_cfg.score_threshold if retrieval_cfg else None) or 0.0

    # Scope retrieval to the application's own knowledge bases -- without this
    # every application searches the whole index and answers from other
    # applications' documents -- and then to the ones the caller may read.
    # Nothing searchable must retrieve nothing, and never reach the model.
    result = await retrieve(
        opensearch,
        embeddings,
        query,
        scope=scope,
        requested_kb_ids=[str(kb.id) for kb in (application.knowledge_bases or [])],
        mode=mode,
        size=top_k,
        min_score=min_score,
        reranker=reranker,
    )
    if result.blocked:
        return ApplicationRetrieval(None, [], mode, model)
    return ApplicationRetrieval([_chunk_from_hit(h) for h in result.hits], result.hits, mode, model, result.rerank)


async def answer_agentic(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    scope: Optional[RetrievalScope] = None,
    user_id: str = "api_user",
    reranker: Any = None,
) -> ApplicationAnswer:
    """Answer through the agentic graph, limited to the caller's permitted attached knowledge bases.

    The graph's chat model is ``llm`` -- the Application's resolved provider -- and
    its retriever is scoped exactly as ``retrieve_for_application`` scopes the
    traditional path, so the strategy cannot be used to widen access.
    """
    decision = scope_application(application, scope)
    retrieval_cfg = application.retrieval_configuration
    model_cfg = application.model_configuration
    mode = retrieval_cfg.mode if retrieval_cfg else RetrievalMode.HYBRID.value
    top_k = top_k_override or (retrieval_cfg.top_k if retrieval_cfg else 8)
    model = model_cfg.llm_model if model_cfg else "llama3.2:1b"

    # With nothing to ground on the graph could answer from the model's general
    # knowledge, which is not what a permission-scoped deployment promises.
    if not decision.permitted:
        return ApplicationAnswer(
            answer=blocked_message(application), mode=mode, scope_decision=decision, model=model, details={"agentic": True}
        )

    service = make_agentic_rag_service(
        opensearch_client=opensearch,
        ollama_client=llm,
        embeddings_client=embeddings,
        langfuse_tracer=None,
        model=model,
        top_k=top_k,
        use_hybrid=mode != RetrievalMode.BM25.value,
        knowledge_base_ids=decision.permitted,
        llm_provider=llm,
        reranker=reranker,
    )
    result = await service.ask(query=query, model=model, user_id=user_id)

    hits = result.get("retrieved_hits", [])
    return ApplicationAnswer(
        answer=result["answer"],
        chunks=[_chunk_from_hit(h) for h in hits],
        hits=hits,
        mode=mode,
        scope_decision=decision,
        model=model,
        details={"agentic": True, "retrieval_attempts": result.get("retrieval_attempts", 0)},
        rerank=result.get("rerank"),
    )


async def answer_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    system_prompt_override: Optional[str] = None,
    scope: Optional[RetrievalScope] = None,
    user_id: str = "api_user",
    reranker: Any = None,
) -> ApplicationAnswer:
    """Retrieve against the application's knowledge bases and generate an answer.

    An agentic Application runs the agentic graph instead of the single retrieve-and-generate pass.
    """
    if is_agentic(application):
        return await answer_agentic(
            application, query, opensearch, embeddings, llm, top_k_override, scope=scope, user_id=user_id, reranker=reranker
        )
    decision = scope_application(application, scope)
    found = await retrieve_application(application, query, opensearch, embeddings, top_k_override, scope, reranker)
    chunks, hits, mode, model = found.chunks, found.hits, found.mode, found.model
    if chunks is None:
        return ApplicationAnswer(
            answer=blocked_message(application), chunks=[], hits=[], mode=mode, scope_decision=decision, model=model
        )

    generation = await llm.generate_rag_answer(
        query=query,
        chunks=chunks,
        model=model,
        system_prompt=effective_system_prompt(application, system_prompt_override),
    )
    answer = generation.get("answer", "") if isinstance(generation, dict) else str(generation)

    return ApplicationAnswer(
        answer=answer, chunks=chunks, hits=hits, mode=mode, scope_decision=decision, model=model, rerank=found.rerank
    )


@dataclass
class PreparedStream:
    """An answer ready to stream: retrieval is done, generation has not started (or, for an agentic
    Application, has finished and is delivered whole).

    Retrieval happens up front, before the HTTP response starts, because a streamed body runs after the
    request-scoped session has closed. Everything here is plain values.
    """

    hits: List[Dict[str, Any]]
    chunks: List[Dict[str, Any]]
    model: str
    rerank: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    # Set when there is nothing to stream token by token: a refusal, or an answer produced whole.
    fixed_answer: Optional[str] = None
    _llm: Any = None
    _query: str = ""
    _system_prompt: Optional[str] = None
    _history: Optional[List[Dict[str, str]]] = None

    async def deltas(self):
        """Yield answer deltas, token by token (a fixed answer is one delta)."""
        if self.fixed_answer is not None:
            yield self.fixed_answer
            return
        # Only passed when there is some, so providers that predate Conversations keep working.
        extra = {"history": self._history} if self._history else {}
        async for chunk in self._llm.generate_rag_answer_stream(
            query=self._query, chunks=self.chunks, model=self.model, system_prompt=self._system_prompt, **extra
        ):
            delta = chunk.get("response", "") if isinstance(chunk, dict) else str(chunk)
            if delta:
                yield delta


async def prepare_stream(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    system_prompt_override: Optional[str] = None,
    scope: Optional[RetrievalScope] = None,
    reranker: Any = None,
    history: Optional[List[Dict[str, str]]] = None,
    search_query: Optional[str] = None,
) -> PreparedStream:
    """Retrieve for the application and return the answer ready to stream.

    The agentic graph produces its answer in one piece, so an agentic Application's answer is generated
    here and streamed as a single delta.

    :param history: earlier turns of a Conversation, given to the model as context.
    :param search_query: what to retrieve with when that differs from ``query`` (a follow-up).
    """
    searching = search_query or query
    if is_agentic(application):
        result = await answer_agentic(
            application, searching, opensearch, embeddings, llm, top_k_override, scope=scope, reranker=reranker
        )
        return PreparedStream(
            hits=result.hits,
            chunks=result.chunks,
            model=result.model,
            rerank=result.rerank,
            details=result.details,
            fixed_answer=result.answer,
        )
    found = await retrieve_application(application, searching, opensearch, embeddings, top_k_override, scope, reranker)
    return PreparedStream(
        hits=found.hits,
        chunks=found.chunks or [],
        model=found.model,
        rerank=found.rerank,
        fixed_answer=blocked_message(application) if found.chunks is None else None,
        _llm=llm,
        _query=query,
        _system_prompt=effective_system_prompt(application, system_prompt_override),
        _history=history,
    )


async def stream_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    system_prompt_override: Optional[str] = None,
    scope: Optional[RetrievalScope] = None,
    reranker: Any = None,
):
    """Yield answer deltas for the application, token by token."""
    prepared = await prepare_stream(
        application, query, opensearch, embeddings, llm, top_k_override, system_prompt_override, scope, reranker
    )
    async for delta in prepared.deltas():
        yield delta


def is_offered_to(application: RAGApplication, scope: Optional[RetrievalScope]) -> bool:
    """Whether an end user with ``scope`` is offered the application.

    Only if they may read *every* knowledge base it attaches: an application that
    would partly refuse them is not shown. One with no knowledge bases has nothing
    to answer from and is not offered either.
    """
    if not application.knowledge_bases:
        return False
    decision = scope_application(application, scope)
    return not decision.dropped
