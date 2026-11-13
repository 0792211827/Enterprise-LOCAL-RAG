"""Conversations: history kept per employee, resumable, renameable, deletable, and private from everyone else.

Runs the chat router over in-memory SQLite with real grants (fixtures in tests/chat_support.py).
"""

import pytest
from src.auth import Role
from tests.chat_support import ask, caller, exchange, frames, stack, start_conversation, world  # noqa: F401

BASE = "/api/v1/chat/conversations"


def test_a_conversation_is_remembered_and_can_be_reopened_with_its_messages_and_citations(stack, world):
    conversation_id, answer = exchange(stack, world, "How much leave?")

    [summary] = stack.http.get(BASE).json()
    assert (summary["id"], summary["application_id"], summary["message_count"]) == (conversation_id, world.apps["Handbook"], 2)
    assert summary["title"] == "How much leave?"  # named after the first question until renamed

    opened = stack.http.get(f"{BASE}/{conversation_id}").json()
    user, assistant = opened["messages"]
    assert (user["role"], user["content"]) == ("user", "How much leave?")
    assert (assistant["role"], assistant["content"]) == ("assistant", "25 days.")
    assert assistant["citations"][0]["document_id"] == world.docs["hr"]
    assert assistant["citations"][0]["passage"] == "Employees get 25 days of annual leave."
    assert answer[-1] == {"type": "done", "message_id": assistant["id"]}


def test_the_history_lists_the_most_recently_active_conversation_first(stack, world):
    first, _ = exchange(stack, world, "first question")
    second, _ = exchange(stack, world, "second question")
    assert [c["id"] for c in stack.http.get(BASE).json()] == [second, first]

    ask(stack, first, "a follow-up")
    assert [c["id"] for c in stack.http.get(BASE).json()] == [first, second]


def test_an_explicit_title_is_kept_and_a_conversation_can_be_renamed(stack, world):
    conversation = start_conversation(stack, world, title="Leave questions")
    ask(stack, conversation["id"], "How much leave?")
    assert stack.http.get(f"{BASE}/{conversation['id']}").json()["title"] == "Leave questions"

    renamed = stack.http.patch(f"{BASE}/{conversation['id']}", json={"title": "Holiday"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Holiday"
    assert [c["title"] for c in stack.http.get(BASE).json()] == ["Holiday"]
    assert stack.http.patch(f"{BASE}/{conversation['id']}", json={"title": ""}).status_code == 422


def test_deleting_a_conversation_removes_it_and_its_messages(stack, world):
    conversation_id, _ = exchange(stack, world)

    assert stack.http.delete(f"{BASE}/{conversation_id}").status_code == 204

    assert stack.http.get(BASE).json() == []
    assert stack.http.get(f"{BASE}/{conversation_id}").status_code == 404
    assert stack.http.delete(f"{BASE}/{conversation_id}").status_code == 404


def test_a_follow_up_is_given_the_earlier_turns_as_context(stack, world):
    conversation = start_conversation(stack, world)
    ask(stack, conversation["id"], "How much leave?")
    ask(stack, conversation["id"], "And does it carry over?")

    first, second = stack.llm.stream_calls
    assert "history" not in first
    assert second["history"] == [
        {"role": "user", "content": "How much leave?"},
        {"role": "assistant", "content": "25 days."},
    ]
    assert second["query"] == "And does it carry over?"
    # Retrieval sees the question it follows, so "it" still finds the leave policy.
    assert "How much leave?" in stack.index.search_unified.call_args.kwargs["query"]


def test_a_follow_up_is_given_only_the_most_recent_messages(stack, world):
    from src.config import AuthSettings, ChatSettings, Settings

    stack.use_settings(Settings(auth=AuthSettings(mode="jwt"), chat=ChatSettings(history_messages=2)))
    conversation = start_conversation(stack, world)
    for question in ("one", "two", "three"):
        ask(stack, conversation["id"], question)

    assert [m["content"] for m in stack.llm.stream_calls[-1]["history"]] == ["two", "25 days."]


# --------------------------------------------------------------------------- #
# Privacy: only the owner reaches a Conversation
# --------------------------------------------------------------------------- #
@pytest.fixture
def alices(stack, world):
    conversation_id, _ = exchange(stack, world, "my private question")
    return conversation_id


@pytest.mark.parametrize("roles", [(Role.QUERIER,), (Role.ADMIN, Role.QUERIER)], ids=["querier", "admin"])
def test_nobody_else_can_read_rename_or_delete_a_conversation(stack, world, alices, roles):
    stack.act_as(caller("mallory", "staff", roles=roles))

    assert stack.http.get(f"{BASE}/{alices}").status_code == 404
    assert stack.http.patch(f"{BASE}/{alices}", json={"title": "pwned"}).status_code == 404
    assert stack.http.delete(f"{BASE}/{alices}").status_code == 404
    send = stack.http.post(f"{BASE}/{alices}/messages", json={"query": "continue as alice"})
    assert send.status_code == 404
    assert stack.http.get(BASE).json() == []

    stack.act_as(caller("alice", "staff"))
    opened = stack.http.get(f"{BASE}/{alices}").json()
    assert opened["title"] == "my private question"
    assert [m["content"] for m in opened["messages"]] == ["my private question", "25 days."]


def test_a_conversation_that_is_someone_elses_looks_like_one_that_does_not_exist(stack, world, alices):
    stack.act_as(caller("mallory", "staff"))
    theirs = stack.http.get(f"{BASE}/{alices}")
    missing = stack.http.get(f"{BASE}/00000000-0000-0000-0000-0000000000ff")
    assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json())


def test_admins_see_counts_but_never_conversation_content(stack, world, alices):
    exchange(stack, world, "another private question")
    stack.act_as(caller("root", roles=(Role.ADMIN, Role.QUERIER)))

    response = stack.http.get("/api/v1/chat-admin/conversation-counts")

    assert response.status_code == 200
    assert response.json() == {
        "conversations": 2,
        "messages": 4,
        "by_application": [
            {"application_id": world.apps["Handbook"], "application_name": "Handbook", "conversations": 2, "messages": 4}
        ],
    }
    assert "private question" not in response.text


def test_the_counts_are_for_admins_only(stack):
    assert stack.http.get("/api/v1/chat-admin/conversation-counts").status_code == 403
