from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from src.dependencies import AgenticRAGDep, AuditDep, PrincipalDep, ScopeDep, TelemetryDep
from src.models import AuditAction
from src.schemas.api.ask import AgenticAskResponse, AskRequest
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
    telemetry: TelemetryDep,
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
    telemetry.observe_application(None)  # a global route: no Application, so the series say "unknown"
    decision = scope.restrict(None)
    with audited_query(
        audit_recorder,
        AuditContext.capture(http_request, principal),
        action=AuditAction.QUERY_ANSWER.value,
        query=request.query,
        decision=decision,
    ) as audit:
        audit.model = request.model
        response, sources, rerank = await _run_agent(request, agentic_rag, principal, decision)
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
        audit.note_rerank(rerank)
        return response


async def _run_agent(request, agentic_rag, principal, decision) -> tuple[AgenticAskResponse, list, Optional[str]]:
    """Run the agent; returns the response, the agent's raw sources and whether reranking was applied or skipped."""
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
        return refusal, [], None

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
        )
        return response, sources, result.get("rerank")

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing question: {str(e)}")
