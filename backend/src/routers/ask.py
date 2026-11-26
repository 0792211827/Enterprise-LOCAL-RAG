import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.dependencies import (
    AuditDep,
    CacheDep,
    EmbeddingsDep,
    LLMProviderDep,
    OpenSearchDep,
    PrincipalDep,
    RerankerDep,
    ScopeDep,
    TelemetryDep,
)
from src.models import AuditAction
from src.schemas.api.ask import AskRequest, AskResponse
from src.services.access import RetrievalScope
from src.services.audit import AuditContext, QueryAudit, audited_query, audited_stream
from src.services.retrieval import embed_or_none, retrieve

logger = logging.getLogger(__name__)

# Two separate routers - one for regular ask, one for streaming
ask_router = APIRouter(tags=["ask"])
stream_router = APIRouter(tags=["stream"])


async def _prepare_chunks_and_sources(
    request: AskRequest,
    opensearch_client,
    embeddings_service,
    scope: Optional[RetrievalScope] = None,
    reranker=None,
    audit: Optional[QueryAudit] = None,
) -> tuple[List[Dict], List[str], List[str]]:
    """Retrieve and prepare chunks for RAG.

    These legacy endpoints search globally; with a restricted ``scope`` the
    search is limited to the knowledge bases the caller may read, and an
    empty scope searches nothing.
    """
    if scope is not None and scope.restrict(None).permitted == []:
        return [], [], []

    # Handle embeddings for hybrid search
    query_embedding = None
    if request.use_hybrid:
        query_embedding = await embed_or_none(embeddings_service, request.query)

    retrieval = await retrieve(
        opensearch_client,
        embeddings_service,
        request.query,
        scope=scope,
        mode="hybrid" if query_embedding is not None else "bm25",
        size=request.top_k,
        categories=request.categories,
        query_embedding=query_embedding,
        reranker=reranker,
    )
    if audit is not None:
        audit.note_rerank(retrieval.rerank)

    # Extract essential data for LLM
    chunks = []
    document_ids = []
    sources_set = set()

    for hit in retrieval.hits:
        document_id = hit.get("document_id", "")
        title = hit.get("title", "")

        # Minimal chunk data for LLM. The title is what the prompt cites,
        # so it must travel with the text. The IDs and score are for the
        # audit trail; the prompt builder ignores them.
        chunks.append(
            {
                "document_id": document_id,
                "title": title,
                "section_title": hit.get("section_title", ""),
                "locator": hit.get("locator"),
                "chunk_text": hit.get("chunk_text", ""),
                "chunk_id": hit.get("chunk_id"),
                "knowledge_base_id": hit.get("knowledge_base_id"),
                "score": hit.get("score"),
            }
        )

        if document_id:
            document_ids.append(document_id)
        # Sources are human-facing: prefer the document title, falling back
        # to its id. Never synthesise an external URL.
        if title or document_id:
            sources_set.add(title or document_id)

    return chunks, list(sources_set), document_ids


@ask_router.post("/ask", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    opensearch_client: OpenSearchDep,
    embeddings_service: EmbeddingsDep,
    llm: LLMProviderDep,
    cache_client: CacheDep,
    principal: PrincipalDep,
    scope: ScopeDep,
    reranker: RerankerDep,
    audit_recorder: AuditDep,
    telemetry: TelemetryDep,
    http_request: Request,
) -> AskResponse:
    """Clean RAG endpoint with exact match caching."""
    scope_token = scope.cache_token()
    telemetry.observe_application(None)  # a global route: no Application, so the series say "unknown"

    with audited_query(
        audit_recorder,
        AuditContext.capture(http_request, principal),
        action=AuditAction.QUERY_ANSWER.value,
        query=request.query,
        decision=scope.restrict(None),
    ) as audit:
        audit.model, audit.cache_hit = request.model, (False if cache_client else None)
        try:
            # Check exact cache first
            cached_response = None
            if cache_client:
                try:
                    cached_response = await cache_client.find_cached_response(request, scope_token)
                    telemetry.record_cache_lookup(bool(cached_response))
                    if cached_response:
                        logger.info("Returning cached response for exact query match")
                        _audit_cache_hit(audit, cached_response)
                        return cached_response
                except Exception as e:
                    logger.warning(f"Cache check failed, proceeding with normal flow: {e}")

            # Generate query embedding for hybrid search if needed
            query_embedding = None

            # Retrieve chunks
            chunks, sources, _ = await _prepare_chunks_and_sources(
                request, opensearch_client, embeddings_service, scope=scope, reranker=reranker, audit=audit
            )

            audit.hits = chunks
            if not chunks:
                response = AskResponse(
                    query=request.query,
                    answer="I couldn't find any relevant information in the papers to answer your question.",
                    sources=[],
                    chunks_used=0,
                    search_mode="bm25" if not request.use_hybrid else "hybrid",
                )
                audit.answer = response.answer
                return response

            # Generate answer
            with telemetry.stage("generate_answer"):
                rag_response = await llm.generate_rag_answer(query=request.query, chunks=chunks, model=request.model)
            answer = rag_response.get("answer", "Unable to generate answer")
            audit.answer = answer

            # Prepare response
            response = AskResponse(
                query=request.query,
                answer=answer,
                sources=sources,
                chunks_used=len(chunks),
                search_mode="bm25" if not request.use_hybrid else "hybrid",
            )

            # Store response in exact match cache
            if cache_client:
                try:
                    await cache_client.store_response(request, response, scope_token)
                except Exception as e:
                    logger.warning(f"Failed to store response in cache: {e}")

            return response

        except Exception as e:
            logger.error(f"Error processing request: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@stream_router.post("/stream")
async def ask_question_stream(
    request: AskRequest,
    opensearch_client: OpenSearchDep,
    embeddings_service: EmbeddingsDep,
    llm: LLMProviderDep,
    cache_client: CacheDep,
    principal: PrincipalDep,
    scope: ScopeDep,
    reranker: RerankerDep,
    audit_recorder: AuditDep,
    telemetry: TelemetryDep,
    http_request: Request,
) -> StreamingResponse:
    """Clean streaming RAG endpoint."""
    telemetry.observe_application(None)

    # Resolved to plain values now: the generator runs after the handler returns.
    scope_token = scope.cache_token()
    audit = QueryAudit(
        audit_recorder,
        AuditContext.capture(http_request, principal),
        action=AuditAction.QUERY_ANSWER.value,
        query=request.query,
        decision=scope.restrict(None),
    )
    audit.model, audit.cache_hit = request.model, (False if cache_client else None)

    async def generate_stream():
        try:
            # Check exact cache first
            if cache_client:
                try:
                    cached_response = await cache_client.find_cached_response(request, scope_token)
                    telemetry.record_cache_lookup(bool(cached_response))
                    if cached_response:
                        logger.info("Returning cached response for exact streaming query match")
                        _audit_cache_hit(audit, cached_response)

                        # Send metadata first (same format as non-cached)
                        metadata_response = {
                            "sources": cached_response.sources,
                            "chunks_used": cached_response.chunks_used,
                            "search_mode": cached_response.search_mode,
                        }
                        yield f"data: {json.dumps(metadata_response)}\n\n"

                        # Stream the cached response in chunks
                        for chunk in cached_response.answer.split():
                            yield f"data: {json.dumps({'chunk': chunk + ' '})}\n\n"

                        # Send completion signal with just the final answer
                        yield f"data: {json.dumps({'answer': cached_response.answer, 'done': True})}\n\n"
                        return
                except Exception as e:
                    logger.warning(f"Cache check failed, proceeding with normal flow: {e}")

            # Retrieve chunks
            chunks, sources, _ = await _prepare_chunks_and_sources(
                request, opensearch_client, embeddings_service, scope=scope, reranker=reranker, audit=audit
            )

            audit.hits = chunks
            if not chunks:
                audit.answer = "No relevant information found."
                yield f"data: {json.dumps({'answer': 'No relevant information found.', 'sources': [], 'done': True})}\n\n"
                return

            # Send metadata first
            search_mode = "bm25" if not request.use_hybrid else "hybrid"
            metadata_response = {"sources": sources, "chunks_used": len(chunks), "search_mode": search_mode}
            yield f"data: {json.dumps(metadata_response)}\n\n"

            # Stream generation
            full_response = ""
            # Not made current: the span stays open across the yields, which the consumer drives from elsewhere.
            stage = telemetry.begin_stage("generate_answer")
            generation_failed = False
            try:
                async for chunk in llm.generate_rag_answer_stream(query=request.query, chunks=chunks, model=request.model):
                    if chunk.get("response"):
                        text_chunk = chunk["response"]
                        if not full_response:
                            telemetry.record_first_token(request.model)
                        full_response += text_chunk
                        audit.answer = full_response
                        yield f"data: {json.dumps({'chunk': text_chunk})}\n\n"

                    if chunk.get("done", False):
                        yield f"data: {json.dumps({'answer': full_response, 'done': True})}\n\n"
                        break
            except Exception:
                generation_failed = True
                raise
            finally:
                stage.end(error=generation_failed)

            # Store response in exact match cache
            if cache_client and full_response:
                try:
                    response_to_cache = AskResponse(
                        query=request.query,
                        answer=full_response,
                        sources=sources,
                        chunks_used=len(chunks),
                        search_mode=search_mode,
                    )
                    await cache_client.store_response(request, response_to_cache, scope_token)
                except Exception as e:
                    logger.warning(f"Failed to store streaming response in cache: {e}")

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            audit.fail(e)
            telemetry.mark_request_failed()  # the status is 200 by now, so the request metric has to be told
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        audited_stream(generate_stream(), audit, error_frame=lambda message: f"data: {json.dumps({'error': message})}\n\n"),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _audit_cache_hit(audit: QueryAudit, cached: AskResponse) -> None:
    """A cached answer was retrieved by an earlier (audited) request; record
    what it cites rather than claiming a retrieval that did not happen."""
    audit.cache_hit = True
    audit.answer = cached.answer
    audit.details = {"cached_sources": list(cached.sources)}
