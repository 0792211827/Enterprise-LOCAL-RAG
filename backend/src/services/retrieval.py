"""The single retrieval seam: every answer and search path retrieves through ``retrieve``.

It owns scope intersection (what the path wants to search, narrowed to what the
caller may read), the fail-closed rule for an empty scope, and the choice of
search method. Callers never build filters or call the index themselves, so
anything that must apply to every path (reranking, Document restrictions) is
added here once.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.models.enums import RetrievalMode
from src.services.access import RetrievalScope, ScopeDecision
from src.services.rerank.client import Reranker

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """What a retrieval returned.

    :param hits: the matching chunks, best first
    :param total: the index's total match count
    :param mode: the search mode actually used (a hybrid request can degrade to bm25)
    :param decision: the scope the search ran under, for the audit record
    :param blocked: nothing was searched because the caller may read none of the requested knowledge bases
    :param rerank: ``"applied"`` when the reranker reordered the hits, ``"skipped"`` when it was configured
        but failed (the hits keep their original order), None when reranking was not in play
    """

    hits: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0
    mode: str = RetrievalMode.HYBRID.value
    decision: Optional[ScopeDecision] = None
    blocked: bool = False
    rerank: Optional[str] = None


async def embed_or_none(embeddings: Any, query: str) -> Optional[List[float]]:
    """Embed ``query``, or None (with a warning) when the embedder fails, so the caller can fall back to keywords.

    For paths that trace the embedding step themselves; others use ``retrieve(fall_back_to_bm25=True)``.
    """
    try:
        vector = await embeddings.embed_query(query)
        logger.info("Generated query embedding for hybrid search")
        return vector
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to generate embeddings, falling back to BM25: %s", exc)
        return None


async def retrieve(
    opensearch: Any,
    embeddings: Any,
    query: str,
    *,
    scope: Optional[RetrievalScope],
    requested_kb_ids: Optional[Sequence[str]] = None,
    mode: str = RetrievalMode.HYBRID.value,
    size: int = 10,
    from_: int = 0,
    categories: Optional[List[str]] = None,
    latest: bool = False,
    min_score: float = 0.0,
    query_embedding: Optional[List[float]] = None,
    fall_back_to_bm25: bool = False,
    reranker: Optional[Reranker] = None,
) -> RetrievalResult:
    """Search the index as ``scope`` allows.

    :param scope: the caller's retrieval permissions; ``None`` applies none
    :param requested_kb_ids: the knowledge bases this path wants to search; ``None``
        is a global search, which becomes exactly what the scope permits
    :param mode: ``bm25``, ``vector`` or ``hybrid``
    :param query_embedding: a vector already computed by the caller (it may be tracing the step)
    :param fall_back_to_bm25: when embedding fails, search by keywords instead of raising
    :param reranker: reorders the fused candidates before the cut to ``size``. It only ever sees hits the
        index already filtered to the permitted scope. A failing endpoint costs nothing but the reordering.
        Later pages (``from_ > 0``) are not reranked: the window would no longer line up.
    :raises Exception: whatever the embedder raises, unless ``fall_back_to_bm25``
    """
    if mode not in (RetrievalMode.BM25.value, RetrievalMode.VECTOR.value):
        mode = RetrievalMode.HYBRID.value
    decision = (scope or RetrievalScope.unrestricted("no scope supplied")).restrict(requested_kb_ids)
    # An empty permitted set must search nothing, never everything. The index
    # client also treats [] as "match nothing", but this path must not depend on that.
    if decision.permitted == []:
        return RetrievalResult(mode=mode, decision=decision, blocked=True)
    kb_ids = decision.permitted

    rerank_in_play = reranker is not None and from_ == 0
    fetch_size = max(size, reranker.candidates) if rerank_in_play else size

    if mode != RetrievalMode.BM25.value and query_embedding is None:
        try:
            query_embedding = await embeddings.embed_query(query)
        except Exception as exc:  # noqa: BLE001 - degrade only when the caller asked for it
            if not fall_back_to_bm25:
                raise
            logger.warning("Failed to generate embeddings, falling back to BM25: %s", exc)
            mode = RetrievalMode.BM25.value
    if mode == RetrievalMode.BM25.value:
        query_embedding = None

    if mode == RetrievalMode.VECTOR.value:
        results = opensearch.search_chunks_vector(
            query_embedding=query_embedding, size=fetch_size, categories=categories, knowledge_base_ids=kb_ids
        )
    else:
        results = opensearch.search_unified(
            query=query,
            query_embedding=query_embedding,
            size=fetch_size,
            from_=from_,
            categories=categories,
            latest=latest,
            use_hybrid=mode == RetrievalMode.HYBRID.value,
            min_score=min_score,
            knowledge_base_ids=kb_ids,
        )

    results = results if isinstance(results, dict) else {}
    hits = results.get("hits", [])

    outcome = None
    if rerank_in_play:
        if len(hits) > 1:
            try:
                hits, outcome = await reranker.rerank(query, hits), "applied"
            except Exception as exc:  # noqa: BLE001 - a down or slow reranker must not stop answers
                logger.warning("Rerank skipped, keeping the original order: %s", exc)
                outcome = "skipped"
        hits = hits[:size]
    return RetrievalResult(hits=hits, total=results.get("total", 0), mode=mode, decision=decision, rerank=outcome)
