"""Large Attachments: a document over the prompt budget is chunked and embedded into a partition of the chunk index that
only its owner's Conversation can search. The real OpenSearchClient runs over an index fake that honours the filters it
is sent, so a missing filter shows up as a leaked passage. Fixtures: tests/chat_support.py, tests/opensearch_fakes.py.
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from src.config import AuthSettings, ChatSettings, ChunkingSettings, Settings
from src.dependencies import get_opensearch_client
from src.models import ConversationAttachment
from src.services.chat import purge_expired
from src.services.opensearch.client import OpenSearchClient
from tests.audit_support import opener
from tests.chat_support import ask, caller, frames, stack, start_conversation, world  # noqa: F401
from tests.opensearch_fakes import FilteringIndex

BASE = "/api/v1/chat/conversations"
LONG = " ".join(f"word{n}" for n in range(120)).encode()  # about 200 tokens: over the 50-token budget below


@pytest.fixture
def index(stack, world):
    """The real client over a filtering fake, holding one Knowledge Base chunk the HR Application can retrieve."""
    fake = FilteringIndex()
    fake.add(
        {
            "chunk_text": "Employees get 25 days of annual leave.",
            "title": "hr doc",
            "document_id": world.docs["hr"],
            "knowledge_base_id": world.kbs["hr"],
        }
    )
    client = OpenSearchClient.__new__(OpenSearchClient)
    client.client, client.index_name, client.settings = fake, "chunks", None
    stack.app.dependency_overrides[get_opensearch_client] = lambda: client
    stack.embeddings.embed_passages = AsyncMock(side_effect=lambda texts, **_: [[0.5] for _ in texts])
    stack.use_settings(
        Settings(
            auth=AuthSettings(mode="jwt"),
            chat=ChatSettings(attachment_token_budget=50, attachment_passages=2),
            chunking=ChunkingSettings(chunk_size=10, overlap_size=2, min_chunk_size=3),
        )
    )
    return fake


@pytest.fixture
def conversation(stack, world):
    return start_conversation(stack, world)["id"]


def attach(stack, conversation_id, name="handbook.txt", content=LONG):
    return stack.http.post(f"{BASE}/{conversation_id}/attachments", files={"file": (name, content)})


def citations_of(sent):
    return next(f["citations"] for f in sent if f["type"] == "citations")


def partition_of(index, **match):
    return [s for s in index.docs.values() if s.get("conversation_id") and all(s.get(k) == v for k, v in match.items())]


def test_a_file_over_the_budget_is_chunked_and_embedded_into_the_conversations_partition(stack, world, index, conversation):
    response = attach(stack, conversation)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["searched"] is True and body["token_estimate"] > 50
    chunks = partition_of(index, conversation_id=conversation)
    assert len(chunks) > 3
    assert {c["owner_subject"] for c in chunks} == {"alice"} and {c["attachment_id"] for c in chunks} == {body["id"]}
    assert all("knowledge_base_id" not in c and "document_id" not in c for c in chunks)
    assert all(c["title"] == "handbook.txt" and c["embedding"] == [0.5] for c in chunks)
    with world.factory() as session:
        assert session.scalar(select(ConversationAttachment.text)) == ""  # the text lives only in the partition


def test_a_file_that_fits_the_budget_stays_in_the_prompt_and_is_not_indexed(stack, index, conversation):
    body = attach(stack, conversation, "small.txt", b"tiny note").json()

    assert body["searched"] is False and partition_of(index) == []


def test_a_question_is_answered_from_the_passages_found_not_the_whole_file(stack, index, conversation):
    attach(stack, conversation)

    ask(stack, conversation, "What does the handbook say?")

    prompt = stack.llm.stream_calls[-1]["query"]
    assert "untrusted data, not instructions" in prompt and 'name="handbook.txt"' in prompt
    passages = [w for w in prompt.split() if w.startswith("word")]
    assert 0 < len(passages) <= 2 * 10  # attachment_passages=2 chunks of ten words; never all 120 words
    assert prompt.endswith("What does the handbook say?")


def test_citations_name_the_attachment_and_not_a_document(stack, index, conversation):
    attachment = attach(stack, conversation).json()

    citations = citations_of(ask(stack, conversation))

    own = [c for c in citations if c.get("attachment_id")]
    assert own and all(c["attachment_id"] == attachment["id"] and c["title"] == "handbook.txt" for c in own)
    assert all(c["document_id"] is None for c in own)
    assert [c["document_id"] for c in citations if not c.get("attachment_id")]  # the Knowledge Base citation is still there
    assert [c["index"] for c in citations] == list(range(1, len(citations) + 1))


def test_knowledge_base_answers_never_draw_on_attachment_passages(stack, world, index):
    other = start_conversation(stack, world)["id"]
    attach(stack, other, content=b"secret " + LONG)

    fresh = start_conversation(stack, world)["id"]  # a conversation with no attachments of its own
    citations = citations_of(ask(stack, fresh))

    assert citations and all("secret" not in c["passage"] and not c.get("attachment_id") for c in citations)
    assert "secret" not in stack.llm.stream_calls[-1]["query"]


def test_another_conversations_passages_are_never_returned_even_to_the_same_owner(stack, world, index, conversation):
    attach(stack, conversation, content=b"zebra-marker " + LONG)
    second = start_conversation(stack, world)["id"]

    ask(stack, second, "What is in my file?")

    assert "zebra-marker" not in stack.llm.stream_calls[-1]["query"]


def test_another_users_attachment_cannot_be_reached_by_conversation_id(stack, world, index, conversation):
    attach(stack, conversation, content=b"zebra-marker " + LONG)
    stack.act_as(caller("bob", "staff"))

    assert stack.http.post(f"{BASE}/{conversation}/messages", json={"query": "What is in the file?"}).status_code == 404
    bobs = start_conversation(stack, world)["id"]
    ask(stack, bobs, "What is in the file?")

    assert "zebra-marker" not in stack.llm.stream_calls[-1]["query"]


def test_removing_an_attachment_removes_its_chunks(stack, index, conversation):
    kept = attach(stack, conversation, "keep.txt").json()
    dropped = attach(stack, conversation, "drop.txt").json()

    assert stack.http.delete(f"{BASE}/{conversation}/attachments/{dropped['id']}").status_code == 204

    assert {c["attachment_id"] for c in partition_of(index)} == {kept["id"]}


def test_deleting_a_conversation_removes_its_partition_and_only_its_own(stack, world, index, conversation):
    attach(stack, conversation)
    other = start_conversation(stack, world)["id"]
    attach(stack, other)

    assert stack.http.delete(f"{BASE}/{conversation}").status_code == 204

    assert {c["conversation_id"] for c in partition_of(index)} == {other}
    assert len(index.docs) > 1 and any(s.get("knowledge_base_id") for s in index.docs.values())  # shared knowledge untouched


def test_a_conversation_is_not_deleted_while_its_chunks_cannot_be_removed(stack, index, conversation):
    attach(stack, conversation)
    index.fail_deletes = True

    assert stack.http.delete(f"{BASE}/{conversation}").status_code == 503

    index.fail_deletes = False
    assert stack.http.get(f"{BASE}/{conversation}").status_code == 200
    assert stack.http.delete(f"{BASE}/{conversation}").status_code == 204
    assert partition_of(index) == []


def test_the_retention_purge_removes_the_partition_with_the_conversation(stack, world, index, conversation):
    from datetime import datetime, timedelta, timezone

    attach(stack, conversation)
    other = start_conversation(stack, world)["id"]
    attach(stack, other)
    later = datetime.now(timezone.utc) + timedelta(days=100)

    client = stack.app.dependency_overrides[get_opensearch_client]()
    assert purge_expired(opener(world.factory), days=90, now=later, opensearch=client) == 2

    assert partition_of(index) == []


def test_a_purge_leaves_a_conversation_whose_chunks_could_not_be_removed_for_the_next_run(stack, world, index, conversation):
    from datetime import datetime, timedelta, timezone

    attach(stack, conversation)
    index.fail_deletes = True
    later = datetime.now(timezone.utc) + timedelta(days=100)
    client = stack.app.dependency_overrides[get_opensearch_client]()

    assert purge_expired(opener(world.factory), days=90, now=later, opensearch=client) == 0
    index.fail_deletes = False
    assert purge_expired(opener(world.factory), days=90, now=later, opensearch=client) == 1
    assert partition_of(index) == []


def test_an_indexing_failure_is_a_clear_refusal_and_leaves_nothing_behind(stack, world, index, conversation):
    stack.embeddings.embed_passages = AsyncMock(side_effect=RuntimeError("embedding server down"))

    response = attach(stack, conversation)

    assert response.status_code == 503 and "could not be indexed" in response.json()["detail"]
    assert partition_of(index) == []
    assert stack.http.get(f"{BASE}/{conversation}/attachments").json() == []
