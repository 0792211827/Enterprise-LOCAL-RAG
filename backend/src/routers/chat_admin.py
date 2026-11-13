"""What admins may see of employee chat: counts, ratings, and only the exchanges users chose to share.

Never a Conversation's content: counts carry no text, ratings carry message identifiers, and the one place text
appears is an exchange whose owner explicitly shared it with the rating.
"""

from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from src.dependencies import SessionDep
from src.repositories import ApplicationRepository, ConversationRepository, FeedbackRepository
from src.schemas.api.chat import (
    ApplicationChatCounts,
    ApplicationRatings,
    ConversationCounts,
    RatingView,
    SharedExchange,
)
from src.services.chat import golden_candidates_yaml, shared_sources

router = APIRouter(prefix="/api/v1/chat-admin", tags=["chat-admin"])

DELETED_APPLICATION = "(deleted application)"


def _names(session) -> Dict[str, str]:
    return {str(a.id): a.name for a in ApplicationRepository(session).get_all(limit=10000)}


@router.get("/conversation-counts", response_model=ConversationCounts)
def conversation_counts(session: SessionDep):
    """How many Conversations and messages exist, per Application. Never their content or owners."""
    names = _names(session)
    by_application = [
        ApplicationChatCounts(
            application_id=str(row["application_id"]),
            application_name=names.get(str(row["application_id"]), DELETED_APPLICATION),
            conversations=row["conversations"],
            messages=row["messages"],
        )
        for row in ConversationRepository(session).counts_by_application()
    ]
    return ConversationCounts(
        conversations=sum(row.conversations for row in by_application),
        messages=sum(row.messages for row in by_application),
        by_application=sorted(by_application, key=lambda row: row.application_name),
    )


@router.get("/ratings/summary", response_model=List[ApplicationRatings])
def ratings_summary(session: SessionDep):
    """Thumbs up and down per Application."""
    names = _names(session)
    return sorted(
        (
            ApplicationRatings(
                application_id=str(row["application_id"]),
                application_name=names.get(str(row["application_id"]), DELETED_APPLICATION),
                up=row["up"],
                down=row["down"],
            )
            for row in FeedbackRepository(session).summary()
        ),
        key=lambda row: row.application_name,
    )


@router.get("/ratings", response_model=List[RatingView])
def ratings(session: SessionDep, application_id: Optional[UUID] = None):
    return [
        RatingView(
            message_id=str(row["feedback"].message_id),
            application_id=str(row["application_id"]),
            rating=row["feedback"].rating,
            comment=row["feedback"].comment,
            shared=row["feedback"].shared,
            rated_at=row["feedback"].updated_at.isoformat(),
        )
        for row in FeedbackRepository(session).ratings(application_id)
    ]


@router.get("/shared-exchanges", response_model=List[SharedExchange])
def shared_exchanges(session: SessionDep, application_id: Optional[UUID] = None):
    """The exchanges users chose to share with admins -- and no others."""
    return [
        SharedExchange(
            message_id=str(row["feedback"].message_id),
            application_id=str(row["application_id"]),
            rating=row["feedback"].rating,
            comment=row["feedback"].comment,
            question=row["question"],
            answer=row["answer"].content,
            sources=shared_sources(row["answer"].citations),
        )
        for row in FeedbackRepository(session).shared_exchanges(application_id)
    ]


@router.get("/shared-exchanges/export")
def export_shared_exchanges(session: SessionDep, application_id: UUID):
    """Shared exchanges of one Application as Golden set candidates (the eval harness's YAML format).

    Each case is the question with the Documents the answer cited as the expected ones -- a starting point for an
    admin to review. No caller identity is included.
    """
    application = ApplicationRepository(session).get_by_id(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    content = golden_candidates_yaml(application, FeedbackRepository(session).shared_exchanges(application_id))
    return Response(
        content=content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{application.slug}-golden-candidates.yaml"'},
    )
