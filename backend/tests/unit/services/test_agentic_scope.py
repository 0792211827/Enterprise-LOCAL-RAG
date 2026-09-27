"""The agentic path and the scope dependency honour retrieval permissions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.auth import Principal, Role, get_principal
from src.config import AuthSettings, Settings
from src.db.interfaces.postgresql import Base
from src.dependencies import (
    ScopeDep,
    get_agentic_rag_service,
    get_database,
    get_db_session,
    get_retrieval_scope,
    get_settings,
)
from src.models import KnowledgeBase
from src.repositories import GrantRepository
from src.services.access import NO_ACCESSIBLE_KNOWLEDGE_MESSAGE, RetrievalScope
from src.services.agents.tools import create_retriever_tool


class TestAgenticService:
    def test_dependency_passes_permitted_ids_to_the_graph(self):
        scope = RetrievalScope(kb_ids=frozenset({"kb-b", "kb-a"}), reason="grants")
        service = get_agentic_rag_service(MagicMock(), MagicMock(), MagicMock(), MagicMock(), Settings(), scope)
        assert service.graph_config.knowledge_base_ids == ["kb-a", "kb-b"]

    def test_unrestricted_scope_leaves_the_retriever_unscoped(self):
        scope = RetrievalScope.unrestricted("authentication disabled")
        service = get_agentic_rag_service(MagicMock(), MagicMock(), MagicMock(), MagicMock(), Settings(), scope)
        assert service.graph_config.knowledge_base_ids is None

    async def test_retriever_tool_scopes_its_search(self):
        opensearch = MagicMock()
        opensearch.search_unified = MagicMock(return_value={"hits": []})
        embeddings = AsyncMock()
        embeddings.embed_query = AsyncMock(return_value=[0.1] * 4)
        tool = create_retriever_tool(opensearch, embeddings, knowledge_base_ids=["kb-a"])
        await tool.ainvoke({"query": "q"})
        assert opensearch.search_unified.call_args.kwargs["knowledge_base_ids"] == ["kb-a"]


class TestAgenticRouter:
    @pytest.fixture
    def client(self):
        import src.main as main

        service = MagicMock()
        service.ask = AsyncMock(return_value={"query": "q", "answer": "a"})
        main.app.dependency_overrides[get_agentic_rag_service] = lambda: service
        yield TestClient(main.app), service, main.app
        main.app.dependency_overrides.clear()

    def test_empty_scope_refuses_without_running_the_agent(self, client):
        http, service, app = client
        app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope(kb_ids=frozenset(), reason="grants")
        response = http.post("/api/v1/ask-agentic", json={"query": "q"})
        assert response.status_code == 200
        assert response.json()["answer"] == NO_ACCESSIBLE_KNOWLEDGE_MESSAGE
        assert response.json()["retrieval_attempts"] == 0
        service.ask.assert_not_called()

    def test_permitted_scope_runs_the_agent(self, client):
        http, service, app = client
        app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope(kb_ids=frozenset({"k"}), reason="grants")
        assert http.post("/api/v1/ask-agentic", json={"query": "q"}).status_code == 200
        service.ask.assert_called_once()


class TestScopeDependency:
    """get_retrieval_scope opens a session only when it has grants to read."""

    def _app(self, settings, session_override=None, database=None):
        app = FastAPI()

        @app.get("/scope")
        def scope(s: ScopeDep):
            return {"unrestricted": s.is_unrestricted, "kb_ids": sorted(s.kb_ids or [])}

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_principal] = lambda: Principal(
            subject="bob", roles=frozenset({Role.QUERIER}), groups=frozenset({"finance"}), auth_method="jwt"
        )
        if session_override is not None:
            app.dependency_overrides[get_db_session] = session_override
        if database is not None:
            app.dependency_overrides[get_database] = lambda: database
        return TestClient(app)

    def test_disabled_mode_needs_no_database(self):
        # No database is configured at all: this would raise if one were touched.
        client = self._app(Settings(auth=AuthSettings(mode="disabled")))
        assert client.get("/scope").json() == {"unrestricted": True, "kb_ids": []}

    def test_enabled_mode_reads_grants(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            kb = KnowledgeBase(name="Finance", slug="finance", index_name="idx")
            session.add(kb)
            session.commit()
            GrantRepository(session).replace_for_knowledge_base(kb.id, [("group", "finance")])
            kb_id = str(kb.id)

        def _session():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        client = self._app(Settings(auth=AuthSettings(mode="jwt")), session_override=_session)
        assert client.get("/scope").json() == {"unrestricted": False, "kb_ids": [kb_id]}
