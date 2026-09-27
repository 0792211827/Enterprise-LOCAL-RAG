"""Shared answer path for a configured RAG application.

Both the admin playground (``POST /api/v1/applications/{id}/ask``) and the
OpenAI-compatible endpoint (``POST /v1/chat/completions``) route through here so
the two can never drift apart -- what the playground proves is exactly what a
copy-pasted snippet will do.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.models.application import RAGApplication
from src.models.enums import RetrievalMode
from src.services.access import RetrievalScope, ScopeDecision

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
class ApplicationAnswer:
    answer: str
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    hits: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = RetrievalMode.HYBRID.value
    scope_decision: Optional[ScopeDecision] = None
    model: Optional[str] = None


async def retrieve_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    top_k_override: int | None = None,
    scope: Optional[RetrievalScope] = None,
) -> tuple[list, list, str, str]:
    """Retrieve chunks scoped to the application's knowledge bases.

    :param scope: the caller's retrieval permissions; ``None`` applies none.
    :returns: ``(chunks, hits, mode, model)``. ``chunks is None`` means there
        was nothing the caller may search -- no knowledge bases attached, or
        none permitted -- and nothing was retrieved; see ``blocked_message``.
    """
    retrieval_cfg = application.retrieval_configuration
    model_cfg = application.model_configuration
    mode = retrieval_cfg.mode if retrieval_cfg else RetrievalMode.HYBRID.value
    top_k = top_k_override or (retrieval_cfg.top_k if retrieval_cfg else 8)
    model = model_cfg.llm_model if model_cfg else "llama3.2:1b"
    min_score = (retrieval_cfg.score_threshold if retrieval_cfg else None) or 0.0

    # Scope retrieval to the application's own knowledge bases -- without this
    # every application searches the whole index and answers from other
    # applications' documents -- and then to the ones the caller may read.
    kb_ids = scope_application(application, scope).permitted

    # Nothing searchable must retrieve nothing, and never reach the model. (The
    # search client also treats an empty list as "match nothing", but this path
    # must not depend on that.)
    if not kb_ids:
        return None, [], mode, model

    if mode == RetrievalMode.BM25.value:
        results = opensearch.search_unified(
            query=query,
            size=top_k,
            use_hybrid=False,
            min_score=min_score,
            knowledge_base_ids=kb_ids,
        )
    elif mode == RetrievalMode.VECTOR.value:
        query_vector = await embeddings.embed_query(query)
        results = opensearch.search_chunks_vector(query_embedding=query_vector, size=top_k, knowledge_base_ids=kb_ids)
    else:  # hybrid
        query_vector = await embeddings.embed_query(query)
        results = opensearch.search_unified(
            query=query,
            query_embedding=query_vector,
            size=top_k,
            use_hybrid=True,
            min_score=min_score,
            knowledge_base_ids=kb_ids,
        )

    hits = results.get("hits", []) if isinstance(results, dict) else []

    chunks = [
        {
            "chunk_text": h.get("chunk_text", ""),
            "title": h.get("title", ""),
            "score": h.get("score", 0.0),
            "document_id": h.get("document_id"),
            "section_title": h.get("section_title"),
        }
        for h in hits
    ]

    return chunks, hits, mode, model


async def answer_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    system_prompt_override: Optional[str] = None,
    scope: Optional[RetrievalScope] = None,
) -> ApplicationAnswer:
    """Retrieve against the application's knowledge bases and generate an answer."""
    decision = scope_application(application, scope)
    chunks, hits, mode, model = await retrieve_for_application(
        application, query, opensearch, embeddings, top_k_override, scope=scope
    )
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
        answer=answer, chunks=chunks, hits=hits, mode=mode, scope_decision=decision, model=model
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
):
    """Yield answer deltas for the application, token by token."""
    chunks, _hits, _mode, model = await retrieve_for_application(
        application, query, opensearch, embeddings, top_k_override, scope=scope
    )
    if chunks is None:
        yield blocked_message(application)
        return

    async for chunk in llm.generate_rag_answer_stream(
        query=query,
        chunks=chunks,
        model=model,
        system_prompt=effective_system_prompt(application, system_prompt_override),
    ):
        delta = chunk.get("response", "") if isinstance(chunk, dict) else str(chunk)
        if delta:
            yield delta
