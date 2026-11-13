"""Run a Golden set against an Application and score it.

Each case goes through the production path: the real scope resolver (as the
case's Principal, if it names one) and the shared application answer module.
Quality metrics (recall@k, MRR) are reported and never fail a run; only
``must_not_return`` violations do, which makes the Golden set a permissions
regression suite as well.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from src.auth import Principal
from src.auth.principal import map_roles
from src.config import Settings
from src.repositories import ApplicationRepository
from src.services.access import RetrievalScope, resolve_scope
from src.services.applications import answer_for_application, retrieve_application, scope_application
from src.services.providers.llm.registry import ProviderRegistry

from .golden import GoldenCase, GoldenSet, PrincipalSpec
from .judge import GroundednessJudge

logger = logging.getLogger(__name__)

# Bump when the report's shape changes, so tooling comparing reports can tell.
REPORT_VERSION = 1


@dataclass
class EvalContext:
    """The collaborators a run needs. Real ones come from the same factories as the API."""

    settings: Settings
    open_session: Callable[[], ContextManager[Session]]
    opensearch: Any
    embeddings: Any
    llm: Any
    providers: Optional[ProviderRegistry] = None
    reranker: Any = None
    judge: Optional[GroundednessJudge] = None


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _matches_document(hit: Dict[str, Any], reference: str) -> bool:
    return _norm(reference) in (_norm(hit.get("document_id")), _norm(hit.get("title")))


def _matches_passage(hit: Dict[str, Any], passage: str) -> bool:
    return _norm(passage) in _norm(hit.get("chunk_text"))


def _principal(spec: PrincipalSpec, settings: Settings) -> Principal:
    raw_roles = frozenset(spec.roles)
    return Principal(
        subject=spec.subject,
        roles=map_roles(raw_roles, settings.auth),
        auth_method="jwt",
        groups=frozenset(spec.groups),
        raw_roles=raw_roles,
    )


def _scope_for(case: GoldenCase, session: Session, settings: Settings) -> RetrievalScope:
    """The case's caller, resolved by the production resolver.

    A case naming no Principal runs as the Application itself (as when it is
    called with its own API key): limited only by its attached Knowledge Bases.
    """
    if case.principal is None:
        return RetrievalScope.unrestricted("eval: no principal named")
    return resolve_scope(_principal(case.principal, settings), session, settings)


def _score(case: GoldenCase, hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recall@k and reciprocal rank of ``hits`` (already cut to k) against the case's expectations."""
    expectations = [(f"document: {d}", lambda h, d=d: _matches_document(h, d)) for d in case.expected_documents]
    expectations += [(f"passage: {p}", lambda h, p=p: _matches_passage(h, p)) for p in case.expected_passages]
    if not expectations:
        return {"expected": None, "recall_at_k": None, "reciprocal_rank": None}

    found = [label for label, matches in expectations if any(matches(hit) for hit in hits)]
    first_rank = next((rank for rank, hit in enumerate(hits, 1) if any(matches(hit) for _, matches in expectations)), None)
    return {
        "expected": {"found": found, "missing": [label for label, _ in expectations if label not in found]},
        "recall_at_k": len(found) / len(expectations),
        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
    }


def _violations(case: GoldenCase, hits: List[Dict[str, Any]]) -> List[str]:
    violations = [f"document: {d}" for d in case.forbidden_documents if any(_matches_document(h, d) for h in hits)]
    violations += [f"passage: {p}" for p in case.forbidden_passages if any(_matches_passage(h, p) for h in hits)]
    return violations


async def _run_case(case: GoldenCase, application_id: UUID, k: int, context: EvalContext, answer: bool) -> Dict[str, Any]:
    with context.open_session() as session:
        scope = _scope_for(case, session, context.settings)
        # Re-read per case: relationships load lazily and sessions are short-lived.
        application = ApplicationRepository(session).get_by_id(application_id)
        llm = (
            context.providers.for_application(application, default=context.llm) if context.providers else context.llm
        )
        if answer:
            result = await answer_for_application(
                application,
                case.question,
                context.opensearch,
                context.embeddings,
                llm,
                top_k_override=k,
                scope=scope,
                reranker=context.reranker,
            )
            hits, decision, answer_text, rerank = result.hits, result.scope_decision, result.answer, result.rerank
        else:
            found = await retrieve_application(
                application, case.question, context.opensearch, context.embeddings, k, scope, context.reranker
            )
            hits, answer_text, rerank = found.hits, None, found.rerank
            decision = scope_application(application, scope)

    hits = hits[:k]
    return {
        "scope": {
            "reason": scope.reason,
            "permitted_kb_ids": sorted(decision.permitted or []),
            "dropped_kb_ids": sorted(decision.dropped),
        },
        "retrieved": [
            {
                "rank": rank,
                "document_id": hit.get("document_id"),
                "title": hit.get("title"),
                "knowledge_base_id": hit.get("knowledge_base_id"),
                "score": hit.get("score"),
            }
            for rank, hit in enumerate(hits, 1)
        ],
        "hits": hits,
        "answer": answer_text,
        "rerank": rerank,
    }


async def _groundedness(case: GoldenCase, entry: Dict[str, Any], hits: List[Dict[str, Any]], context: EvalContext) -> Dict[str, Any]:
    """The judge's score for one answer, or why there is none. A judge failure is recorded, never raised or passed."""
    unscored = {"score": None, "status": "unscored"}
    if entry["error"] is not None:
        return {**unscored, "reason": "the case failed before an answer was produced"}
    if entry["answer"] is None:
        return {**unscored, "reason": "no answer was generated"}
    if not hits:
        return {**unscored, "reason": "nothing was retrieved to be grounded in"}
    try:
        score = await context.judge.judge(case.question, entry["answer"], [hit.get("chunk_text", "") for hit in hits])
    except Exception as exc:  # noqa: BLE001 - a noisy or unreachable judge must not sink the run
        logger.warning("Groundedness judge failed on case %s: %s", case.id, exc)
        return {**unscored, "reason": f"{type(exc).__name__}: {exc}"}
    return {"score": round(float(score), 6), "status": "scored"}


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


async def run_golden_set(
    golden: GoldenSet,
    application_slug: str,
    context: EvalContext,
    k: Optional[int] = None,
    answer: bool = True,
    source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Score ``application_slug`` against ``golden``; returns the report.

    :param k: how many passages count; defaults to the Golden set's ``k``, then the Application's top-k
    :param answer: also generate answers (the full answer path); false retrieves only
    :raises LookupError: the Application does not exist
    """
    with context.open_session() as session:
        application = ApplicationRepository(session).get_by_slug(application_slug)
        if application is None:
            raise LookupError(f"No application with slug '{application_slug}'")
        retrieval = application.retrieval_configuration
        effective_k = k or golden.k or (retrieval.top_k if retrieval else 8)
        application_id = application.id
        header = {
            "slug": application.slug,
            "id": str(application.id),
            "strategy": application.rag_strategy,
            "retrieval_mode": retrieval.mode if retrieval else "hybrid",
            "model": application.model_configuration.llm_model if application.model_configuration else None,
        }

    cases: List[Dict[str, Any]] = []
    for case in golden.cases:
        entry: Dict[str, Any] = {
            "id": case.id,
            "question": case.question,
            "principal": (
                {"subject": case.principal.subject, "groups": list(case.principal.groups), "roles": list(case.principal.roles)}
                if case.principal
                else None
            ),
            "error": None,
        }
        hits: List[Dict[str, Any]] = []
        try:
            outcome = await _run_case(case, application_id, effective_k, context, answer)
            hits = outcome.pop("hits")
            entry.update(outcome)
        except Exception as exc:  # noqa: BLE001 - one failing case must not hide the others
            logger.warning("Case %s failed: %s", case.id, exc)
            entry.update(error=f"{type(exc).__name__}: {exc}", scope=None, retrieved=[], answer=None, rerank=None)

        entry.update(_score(case, hits))
        if entry["error"] is not None and case.has_expectations:
            entry.update(recall_at_k=0.0, reciprocal_rank=0.0)
        entry["must_not_return"] = {"violations": _violations(case, hits)} if case.has_permission_check else None
        entry["groundedness"] = await _groundedness(case, entry, hits, context) if context.judge else None
        cases.append(entry)

    scored = [c for c in cases if c["recall_at_k"] is not None]
    grounded = [c["groundedness"]["score"] for c in cases if c["groundedness"] and c["groundedness"]["score"] is not None]
    checked = [c for c in cases if c["must_not_return"] is not None]
    violations = sum(len(c["must_not_return"]["violations"]) > 0 for c in checked)
    errors = sum(c["error"] is not None for c in checked)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "golden_set": {
            "name": golden.name,
            "version": golden.version,
            "sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None,
        },
        "application": header,
        "k": effective_k,
        "reranking": "enabled" if context.reranker is not None else "disabled",
        "metrics": {
            "recall_at_k": _mean([c["recall_at_k"] for c in scored]),
            "mrr": _mean([c["reciprocal_rank"] for c in scored]),
            "groundedness": _mean(grounded),
            "groundedness_scored": len(grounded) if context.judge else None,
            "cases_total": len(cases),
            "cases_scored": len(scored),
        },
        "permissions": {
            "cases_checked": len(checked),
            "violations": violations,
            "errors": errors,
            "passed": violations == 0 and errors == 0,
        },
        "cases": cases,
    }
