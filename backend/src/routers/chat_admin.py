"""What admins may see of employee chat: counts only. Never a Conversation's content or owners."""

from typing import Dict

from fastapi import APIRouter
from src.dependencies import SessionDep
from src.repositories import ApplicationRepository, ConversationRepository
from src.schemas.api.chat import ApplicationChatCounts, ConversationCounts

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
