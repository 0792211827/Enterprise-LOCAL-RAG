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

NO_KNOWLEDGE_BASE_MESSAGE = (
    "This assistant has no knowledge bases attached yet, so there is nothing to "
    "answer from. Attach a knowledge base with at least one ingested document."
)


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


async def retrieve_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    top_k_override: int | None = None,
) -> tuple[list, list, str, str]:
    """Retrieve chunks scoped to the application's knowledge bases.

    :returns: ``(chunks, hits, mode, model)``. An empty ``hits`` with no
        knowledge bases attached is signalled by ``chunks`` being ``None``.
    """
    retrieval_cfg = application.retrieval_configuration
    model_cfg = application.model_configuration
    mode = retrieval_cfg.mode if retrieval_cfg else RetrievalMode.HYBRID.value
    top_k = top_k_override or (retrieval_cfg.top_k if retrieval_cfg else 8)
    model = model_cfg.llm_model if model_cfg else "llama3.2:1b"
    min_score = (retrieval_cfg.score_threshold if retrieval_cfg else None) or 0.0

    # Scope retrieval to the application's own knowledge bases. Without this
    # every application searches the whole index and answers from other
    # applications' documents.
    kb_ids = [str(kb.id) for kb in (application.knowledge_bases or [])]

    # An application with no knowledge bases must retrieve nothing. Falling
    # through would pass an empty filter list, which every search path treats as
    # "unscoped" -- i.e. it would answer from every other application's
    # documents, the exact leak this scoping exists to close.
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
) -> ApplicationAnswer:
    """Retrieve against the application's knowledge bases and generate an answer."""
    chunks, hits, mode, model = await retrieve_for_application(application, query, opensearch, embeddings, top_k_override)
    if chunks is None:
        return ApplicationAnswer(answer=NO_KNOWLEDGE_BASE_MESSAGE, chunks=[], hits=[], mode=mode)

    generation = await llm.generate_rag_answer(
        query=query,
        chunks=chunks,
        model=model,
        system_prompt=effective_system_prompt(application, system_prompt_override),
    )
    answer = generation.get("answer", "") if isinstance(generation, dict) else str(generation)

    return ApplicationAnswer(answer=answer, chunks=chunks, hits=hits, mode=mode)


async def stream_for_application(
    application: RAGApplication,
    query: str,
    opensearch: Any,
    embeddings: Any,
    llm: Any,
    top_k_override: int | None = None,
    system_prompt_override: Optional[str] = None,
):
    """Yield answer deltas for the application, token by token."""
    chunks, _hits, _mode, model = await retrieve_for_application(application, query, opensearch, embeddings, top_k_override)
    if chunks is None:
        yield NO_KNOWLEDGE_BASE_MESSAGE
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
