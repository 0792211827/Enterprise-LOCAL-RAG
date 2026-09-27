"""Legacy global endpoints (/ask, /stream, /hybrid-search) honour the scope,
and cached answers never cross permission boundaries."""

from unittest.mock import MagicMock

import pytest
import src.main as main
from src.dependencies import get_retrieval_scope
from src.schemas.api.ask import AskRequest
from src.services.access import RetrievalScope
from src.services.cache.client import CacheClient


@pytest.fixture
def with_scope():
    def _set(*kb_ids):
        main.app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope(
            kb_ids=frozenset(kb_ids), reason="grants"
        )

    yield _set
    main.app.dependency_overrides.pop(get_retrieval_scope, None)


def _search():
    return main.app.state.opensearch_client.search_unified


class TestAsk:
    async def test_unrestricted_searches_globally(self, client):
        await client.post("/api/v1/ask", json={"query": "q"})
        assert _search().call_args.kwargs["knowledge_base_ids"] is None

    async def test_restricted_searches_only_permitted(self, client, with_scope):
        with_scope("kb-b", "kb-a")
        response = await client.post("/api/v1/ask", json={"query": "q"})
        assert response.status_code == 200
        assert _search().call_args.kwargs["knowledge_base_ids"] == ["kb-a", "kb-b"]

    async def test_empty_scope_searches_nothing(self, client, with_scope):
        with_scope()
        response = await client.post("/api/v1/ask", json={"query": "q"})
        assert response.status_code == 200
        assert response.json()["chunks_used"] == 0
        _search().assert_not_called()
        main.app.state.ollama_client.generate_rag_answer.assert_not_called()


class TestStream:
    async def test_empty_scope_searches_nothing(self, client, with_scope):
        with_scope()
        response = await client.post("/api/v1/stream", json={"query": "q"})
        assert "No relevant information found." in response.text
        _search().assert_not_called()

    async def test_restricted_searches_only_permitted(self, client, with_scope):
        with_scope("kb-a")
        await client.post("/api/v1/stream", json={"query": "q"})
        assert _search().call_args.kwargs["knowledge_base_ids"] == ["kb-a"]


class TestHybridSearch:
    async def test_restricted(self, client, with_scope):
        with_scope("kb-a")
        response = await client.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False})
        assert response.status_code == 200
        assert _search().call_args.kwargs["knowledge_base_ids"] == ["kb-a"]

    async def test_unrestricted(self, client):
        await client.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False})
        assert _search().call_args.kwargs["knowledge_base_ids"] is None


class TestCacheKeys:
    @pytest.fixture
    def cache(self):
        settings = MagicMock()
        settings.ttl_hours = 6
        return CacheClient(MagicMock(), settings)

    REQUEST = AskRequest(query="What is RAG?", top_k=3, use_hybrid=True, categories=["b", "a"])

    def test_unscoped_key_is_unchanged_from_before_scoping(self, cache):
        # Pinned value computed with the pre-S3 key function: default deployments
        # keep every existing cache entry.
        assert cache._generate_cache_key(self.REQUEST) == "exact_cache:9ad60887dee3b092"
        assert cache._generate_cache_key(self.REQUEST, None) == "exact_cache:9ad60887dee3b092"

    def test_different_scopes_never_share_entries(self, cache):
        a = RetrievalScope(kb_ids=frozenset({"kb-a"}), reason="grants").cache_token()
        b = RetrievalScope(kb_ids=frozenset({"kb-b"}), reason="grants").cache_token()
        keys = {cache._generate_cache_key(self.REQUEST, token) for token in (None, a, b)}
        assert len(keys) == 3

    def test_identical_scopes_share_entries(self, cache):
        one = RetrievalScope(kb_ids=frozenset({"x", "y"}), reason="grants").cache_token()
        two = RetrievalScope(kb_ids=frozenset({"y", "x"}), reason="grants").cache_token()
        assert cache._generate_cache_key(self.REQUEST, one) == cache._generate_cache_key(self.REQUEST, two)

    async def test_ask_passes_scope_token_to_cache(self, client, with_scope, monkeypatch):
        cache = MagicMock()

        async def _miss(*args, **kwargs):
            return None

        async def _store(*args, **kwargs):
            return True

        cache.find_cached_response = MagicMock(side_effect=_miss)
        cache.store_response = MagicMock(side_effect=_store)
        monkeypatch.setattr(main.app.state, "cache_client", cache)
        with_scope("kb-a")
        await client.post("/api/v1/ask", json={"query": "q"})
        expected = RetrievalScope(kb_ids=frozenset({"kb-a"}), reason="grants").cache_token()
        assert cache.find_cached_response.call_args.args[1] == expected
        assert cache.store_response.call_args.args[2] == expected
