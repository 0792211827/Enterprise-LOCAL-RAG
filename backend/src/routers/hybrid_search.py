import logging

from fastapi import APIRouter, HTTPException, Request
from src.dependencies import AuditDep, EmbeddingsDep, OpenSearchDep, RerankerDep, ScopeDep
from src.models import AuditAction
from src.schemas.api.search import HybridSearchRequest, SearchHit, SearchResponse
from src.services.audit import AuditContext, audited_query
from src.services.retrieval import embed_or_none, retrieve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hybrid-search", tags=["hybrid-search"])


@router.post("/", response_model=SearchResponse)
async def hybrid_search(
    request: HybridSearchRequest,
    opensearch_client: OpenSearchDep,
    embeddings_service: EmbeddingsDep,
    scope: ScopeDep,
    reranker: RerankerDep,
    audit_recorder: AuditDep,
    http_request: Request,
) -> SearchResponse:
    """
    Hybrid search endpoint supporting multiple search modes.

    Results are limited to the knowledge bases the caller may read.
    """
    decision = scope.restrict(None)
    with audited_query(
        audit_recorder,
        AuditContext.capture(http_request),
        action=AuditAction.QUERY_SEARCH.value,
        query=request.query,
        decision=decision,
    ) as audit:
        response = await _search(request, opensearch_client, embeddings_service, scope, reranker, audit)
        audit.hits = [hit.model_dump() for hit in response.hits]
        return response


async def _search(request, opensearch_client, embeddings_service, scope, reranker, audit) -> SearchResponse:
    try:
        if not opensearch_client.health_check():
            raise HTTPException(status_code=503, detail="Search service is currently unavailable")

        query_embedding = await embed_or_none(embeddings_service, request.query) if request.use_hybrid else None

        logger.info(f"Hybrid search: '{request.query}' (hybrid: {request.use_hybrid and query_embedding is not None})")

        result = await retrieve(
            opensearch_client,
            embeddings_service,
            request.query,
            scope=scope,
            mode="hybrid" if query_embedding is not None else "bm25",
            size=request.size,
            from_=request.from_,
            categories=request.categories,
            latest=request.latest_papers,
            min_score=request.min_score,
            query_embedding=query_embedding,
            reranker=reranker,
        )
        audit.note_rerank(result.rerank)
        results = {"total": result.total, "hits": result.hits}

        hits = []
        for hit in results.get("hits", []):
            hits.append(
                SearchHit(
                    document_id=hit.get("document_id", ""),
                    knowledge_base_id=hit.get("knowledge_base_id"),
                    title=hit.get("title", ""),
                    score=hit.get("score", 0.0),
                    highlights=hit.get("highlights"),
                    chunk_text=hit.get("chunk_text"),
                    chunk_id=hit.get("chunk_id"),
                    section_title=hit.get("section_title"),
                )
            )

        search_response = SearchResponse(
            query=request.query,
            total=results.get("total", 0),
            hits=hits,
            size=request.size,
            **{"from": request.from_},
            search_mode="hybrid" if (request.use_hybrid and query_embedding) else "bm25",
        )

        logger.info(f"Search completed: {search_response.total} results returned")
        return search_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hybrid search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
