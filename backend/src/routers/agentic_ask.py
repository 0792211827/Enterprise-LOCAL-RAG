from fastapi import APIRouter, HTTPException, Request
from src.dependencies import AgenticRAGDep, AuditDep, LangfuseDep, PrincipalDep, ScopeDep
from src.models import AuditAction
from src.schemas.api.ask import AgenticAskResponse, AskRequest, FeedbackRequest, FeedbackResponse
from src.services.access import NO_ACCESSIBLE_KNOWLEDGE_MESSAGE
from src.services.audit import AuditContext, audited_query

router = APIRouter(prefix="/api/v1", tags=["agentic-rag"])


@router.post("/ask-agentic", response_model=AgenticAskResponse)
async def ask_agentic(
    request: AskRequest,
    agentic_rag: AgenticRAGDep,
    principal: PrincipalDep,
    scope: ScopeDep,
    audit_recorder: AuditDep,
    http_request: Request,
) -> AgenticAskResponse:
    """
    Agentic RAG endpoint with intelligent retrieval and query refinement.

    Features:
    - Decides if retrieval is needed
    - Grades document relevance
    - Rewrites queries if needed
    - Provides reasoning transparency

    The agent will automatically:
    1. Determine if the question requires research paper retrieval
    2. If needed, search for relevant papers
    3. Grade retrieved documents for relevance
    4. Rewrite the query if documents aren't relevant
    5. Generate an answer with citations

    Args:
        request: Question and parameters
        agentic_rag: Injected agentic RAG service

    Returns:
        Answer with sources and reasoning steps

    Raises:
        HTTPException: If processing fails
    """
    decision = scope.restrict(None)
    with audited_query(
        audit_recorder,
        AuditContext.capture(http_request, principal),
        action=AuditAction.QUERY_ANSWER.value,
        query=request.query,
        decision=decision,
    ) as audit:
        audit.model = request.model
        response, sources = await _run_agent(request, agentic_rag, principal, decision)
        audit.answer = response.answer
        # The agent reports the graded sources it grounded on, not raw chunks.
        grounded = [s for s in sources if isinstance(s, dict)]
        audit.hits = (
            [{"document_id": s.get("document_id"), "score": s.get("relevance_score")} for s in grounded]
            if grounded
            else None
        )
        audit.details = {
            "agentic": True,
            "retrieval_attempts": response.retrieval_attempts,
            "cited_sources": [s if isinstance(s, str) else s.get("title") or s.get("document_id") for s in sources],
        }
        return response


async def _run_agent(request, agentic_rag, principal, decision) -> tuple[AgenticAskResponse, list]:
    """Run the agent; returns the response and the agent's raw sources."""
    # A caller who may read nothing gets a refusal, not an agent run: with no
    # documents to ground on, the agent could answer from the model's general
    # knowledge, which is not what a permission-scoped deployment promises.
    if decision.permitted == []:
        refusal = AgenticAskResponse(
            query=request.query,
            answer=NO_ACCESSIBLE_KNOWLEDGE_MESSAGE,
            sources=[],
            chunks_used=0,
            search_mode="hybrid" if request.use_hybrid else "bm25",
            reasoning_steps=["No accessible knowledge bases for this caller; retrieval skipped."],
            retrieval_attempts=0,
        )
        return refusal, []

    try:
        result = await agentic_rag.ask(
            query=request.query,
            model=request.model,
            user_id=principal.subject,
        )

        sources = result.get("sources", [])
        response = AgenticAskResponse(
            query=result["query"],
            answer=result["answer"],
            sources=sources,
            chunks_used=request.top_k,
            search_mode="hybrid" if request.use_hybrid else "bm25",
            reasoning_steps=result.get("reasoning_steps", []),
            retrieval_attempts=result.get("retrieval_attempts", 0),
            rewritten_query=result.get("rewritten_query"),
            trace_id=result.get("trace_id"),
        )
        return response, sources

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing question: {str(e)}")


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    request: FeedbackRequest,
    langfuse_tracer: LangfuseDep,
) -> FeedbackResponse:
    """
    Submit user feedback for an agentic RAG response.

    This endpoint allows users to rate the quality of answers and provide
    optional comments. Feedback is tracked in Langfuse for continuous improvement.

    Args:
        request: Feedback data including trace_id, score, and optional comment
        langfuse_tracer: Injected Langfuse tracer service

    Returns:
        FeedbackResponse indicating success or failure

    Raises:
        HTTPException: If feedback submission fails
    """
    try:
        if not langfuse_tracer:
            raise HTTPException(
                status_code=503,
                detail="Langfuse tracing is disabled. Cannot submit feedback."
            )

        success = langfuse_tracer.submit_feedback(
            trace_id=request.trace_id,
            score=request.score,
            comment=request.comment,
        )

        if success:
            # Flush to ensure feedback is sent immediately
            langfuse_tracer.flush()

            return FeedbackResponse(
                success=True,
                message="Feedback recorded successfully"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to submit feedback to Langfuse"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error submitting feedback: {str(e)}"
        )
