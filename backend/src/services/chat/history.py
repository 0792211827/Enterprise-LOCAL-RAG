"""What a follow-up question carries from earlier in its Conversation."""

from typing import Dict, List, Optional, Sequence

from src.models.chat import ConversationMessage


def history_for_prompt(messages: Sequence[ConversationMessage], limit: int) -> List[Dict[str, str]]:
    """The latest ``limit`` messages as chat turns, oldest first. Zero turns history off."""
    if limit <= 0:
        return []
    return [{"role": message.role, "content": message.content} for message in messages[-limit:]]


def retrieval_query(query: str, history: Sequence[Dict[str, str]]) -> Optional[str]:
    """The text to search with: the question it follows plus this one, so "and does it carry over?" still finds
    the leave policy. ``None`` when there is nothing to follow, meaning search with the question as asked."""
    previous = next((turn["content"] for turn in reversed(history) if turn["role"] == "user"), None)
    return f"{previous}\n{query}" if previous else None
