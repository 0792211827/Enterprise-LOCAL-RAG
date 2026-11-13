"""The private partition of the chunk index: Knowledge Base searches never see it, and a partition search sees only the
owner's own Conversation. The real client runs over an index fake that honours the filters it is sent."""

import pytest
from src.services.opensearch.client import OpenSearchClient
from tests.opensearch_fakes import FilteringIndex

VECTOR = [0.1] * 8
KB = "kb-1"


@pytest.fixture(params=["portable", "lucene"])
def world(request):
    fake = FilteringIndex()
    if request.param == "lucene":
        fake.indices.get_mapping = lambda index: {
            "chunks-v1": {"mappings": {"properties": {"embedding": {"method": {"engine": "lucene"}}}}}
        }
    client = OpenSearchClient.__new__(OpenSearchClient)
    client.client, client.index_name, client.settings = fake, "chunks", None
    fake.add({"chunk_text": "kb passage", "knowledge_base_id": KB, "document_id": "d1"}, "kb")
    for owner, conversation in [("alice", "c1"), ("alice", "c2"), ("bob", "c3")]:
        fake.add(
            {"chunk_text": f"{owner}-{conversation}", "owner_subject": owner, "conversation_id": conversation, "attachment_id": "a"},
            f"{owner}-{conversation}",
        )
    return client


def ids(result):
    return {hit["chunk_id"] for hit in result["hits"]}


@pytest.mark.parametrize("scope", [None, [KB]])
def test_knowledge_base_searches_never_return_attachment_chunks_on_any_path(world, scope):
    assert ids(world.search_unified("q", size=10, use_hybrid=False, knowledge_base_ids=scope)) == {"kb"}
    assert ids(world.search_unified("q", VECTOR, size=10, knowledge_base_ids=scope)) == {"kb"}
    assert ids(world.search_chunks_hybrid("q", VECTOR, size=10, knowledge_base_ids=scope)) == {"kb"}
    assert ids(world.search_chunks_vector(VECTOR, size=10, knowledge_base_ids=scope)) == {"kb"}
    assert ids(world.search_papers("q", size=10)) == {"kb"}


def test_a_partition_search_returns_only_the_owners_own_conversation(world):
    found = world.search_attachment_passages("q", VECTOR, owner_subject="alice", conversation_id="c1", size=10)

    assert ids(found) == {"alice-c1"}


@pytest.mark.parametrize("owner, conversation", [("bob", "c1"), ("alice", "c3"), ("carol", "c1"), ("alice", "nope")])
def test_a_partition_search_with_the_wrong_owner_or_conversation_finds_nothing(world, owner, conversation):
    assert ids(world.search_attachment_passages("q", VECTOR, owner_subject=owner, conversation_id=conversation, size=10)) == set()


@pytest.mark.parametrize("owner, conversation", [("", "c1"), ("alice", ""), (None, "c1"), ("alice", None)])
def test_a_partition_search_without_both_keys_is_refused_rather_than_widened(world, owner, conversation):
    with pytest.raises(ValueError):
        world.search_attachment_passages("q", VECTOR, owner_subject=owner, conversation_id=conversation, size=10)


def test_deleting_a_conversations_chunks_leaves_everything_else(world):
    world.delete_attachment_chunks("c1")

    assert set(world.client.docs) == {"kb", "alice-c2", "bob-c3"}


def test_deleting_one_attachment_needs_its_conversation_too(world):
    world.delete_attachment_chunks("c1", attachment_id="a")

    assert set(world.client.docs) == {"kb", "alice-c2", "bob-c3"}
    with pytest.raises(ValueError):
        world.delete_attachment_chunks("")


def test_a_failed_delete_is_raised_not_swallowed(world):
    world.client.fail_deletes = True
    with pytest.raises(ConnectionError):
        world.delete_attachment_chunks("c1")


def test_an_index_built_before_partitions_gains_the_fields_in_place(world):
    from unittest.mock import MagicMock

    world.client = MagicMock()
    world.client.indices.get_mapping.return_value = {"chunks-v1": {"mappings": {"properties": {"knowledge_base_id": {}}}}}

    world.ensure_additive_fields()

    body = world.client.indices.put_mapping.call_args.kwargs["body"]
    assert set(body["properties"]) == {"conversation_id", "owner_subject", "attachment_id"}

    world.client.indices.put_mapping.reset_mock()
    world.client.indices.get_mapping.return_value = {"chunks-v1": {"mappings": {"properties": {**body["properties"]}}}}
    world.ensure_additive_fields()
    world.client.indices.put_mapping.assert_not_called()
