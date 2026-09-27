"""The audit middleware: control-plane changes and access denials."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from src.auth import Role, require_role
from src.dependencies import get_db_session, get_embeddings_service, get_llm_provider, get_opensearch_client
from src.middleware import RequestContextMiddleware
from src.middleware.audit import AuditMiddleware, classify, resource_type_of
from src.routers import applications, knowledge_bases
from src.services.audit import DatabaseAuditRecorder

from tests.audit_support import events, opener, sqlite_session_factory


class TestClassify:
    @pytest.mark.parametrize(
        "method, route, status, expected",
        [
            ("POST", "/api/v1/knowledge-bases", 201, ("admin.mutation", "ok")),
            ("DELETE", "/api/v1/knowledge-bases/{kb_id}", 404, ("admin.mutation", "error")),
            ("PUT", "/api/v1/knowledge-bases/{kb_id}/grants", 403, ("admin.mutation", "denied")),
            ("GET", "/api/v1/knowledge-bases", 200, None),
            ("GET", "/api/v1/knowledge-bases", 403, ("access.denied", "denied")),
            ("POST", "/api/v1/ask", 200, None),  # recorded by the handler
            ("POST", "/api/v1/ask", 403, ("access.denied", "denied")),  # the handler never ran
            ("POST", "/v1/chat/completions", 401, ("access.denied", "denied")),
            ("POST", "/api/v1/feedback", 200, None),
            ("POST", None, 404, None),  # no route matched
        ],
    )
    def test_classify(self, method, route, status, expected):
        assert classify(method, route, status) == expected

    def test_resource_type(self):
        assert resource_type_of("/api/v1/knowledge-bases/{kb_id}/grants") == "knowledge-bases"
        assert resource_type_of("/v1/chat/completions") is None


@pytest.fixture
def factory():
    return sqlite_session_factory()


@pytest.fixture
def client(factory):
    """Admin routers on SQLite, guarded and wrapped exactly as main.py does."""
    app = FastAPI()
    admin = [Depends(require_role(Role.ADMIN))]
    app.include_router(knowledge_bases.router, dependencies=admin)
    app.include_router(applications.router, dependencies=admin)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)

    def _session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    opensearch = MagicMock()
    opensearch.search_unified = MagicMock(return_value={"hits": []})
    llm = AsyncMock()
    llm.generate_rag_answer = AsyncMock(return_value={"answer": "a"})
    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_opensearch_client] = lambda: opensearch
    app.dependency_overrides[get_embeddings_service] = lambda: AsyncMock()
    app.dependency_overrides[get_llm_provider] = lambda: llm
    app.state.audit_recorder = DatabaseAuditRecorder(opener(factory), level="metadata")
    return TestClient(app)


def _mutations(factory):
    return [e for e in events(factory) if e.action == "admin.mutation"]


class TestMutations:
    def test_create_is_recorded_with_route_status_and_caller(self, client, factory):
        response = client.post("/api/v1/knowledge-bases", json={"name": "Finance"}, headers={"X-Request-ID": "rid-7"})
        assert response.status_code == 201

        [event] = _mutations(factory)
        assert (event.http_method, event.route, event.status_code, event.outcome) == (
            "POST",
            "/api/v1/knowledge-bases",
            201,
            "ok",
        )
        assert (event.resource_type, event.resource_id) == ("knowledge-bases", None)
        assert (event.actor_subject, event.request_id) == ("api_user", "rid-7")

    def test_delete_and_grants_name_the_resource(self, client, factory):
        kb = client.post("/api/v1/knowledge-bases", json={"name": "Finance"}).json()["id"]
        client.put(f"/api/v1/knowledge-bases/{kb}/grants", json={"grants": [{"principal_type": "everyone"}]})
        client.delete(f"/api/v1/knowledge-bases/{kb}")

        _create, grants, delete = _mutations(factory)
        assert (grants.route, grants.resource_id) == ("/api/v1/knowledge-bases/{kb_id}/grants", kb)
        assert grants.details == {"path_params": {"kb_id": kb}}
        assert (delete.route, delete.status_code, delete.resource_id) == ("/api/v1/knowledge-bases/{kb_id}", 204, kb)

    def test_failed_mutation_is_an_error(self, client, factory):
        client.patch("/api/v1/knowledge-bases/00000000-0000-0000-0000-000000000000", json={"name": "x"})
        [event] = _mutations(factory)
        assert (event.outcome, event.status_code) == ("error", 404)

    def test_reads_are_not_recorded(self, client, factory):
        client.get("/api/v1/knowledge-bases")
        assert events(factory) == []

    def test_playground_ask_is_recorded_once_as_a_query(self, client, factory):
        kb = client.post("/api/v1/knowledge-bases", json={"name": "Finance"}).json()["id"]
        app_id = client.post("/api/v1/applications", json={"name": "Bot", "knowledge_base_ids": [kb]}).json()["id"]
        client.post(f"/api/v1/applications/{app_id}/ask", json={"query": "q"})

        asks = [e for e in events(factory) if e.route == "/api/v1/applications/{application_id}/ask"]
        assert [e.action for e in asks] == ["query.answer"]

    def test_write_failure_never_changes_the_response(self, client):
        @contextmanager
        def broken():
            session = MagicMock()
            session.commit.side_effect = RuntimeError("database is down")
            yield session

        client.app.state.audit_recorder = DatabaseAuditRecorder(broken, fail_mode="deny")
        assert client.post("/api/v1/knowledge-bases", json={"name": "Finance"}).status_code == 201

