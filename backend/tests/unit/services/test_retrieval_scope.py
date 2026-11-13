"""The retrieval scope resolver and scope intersection."""

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.auth import Principal, Role
from src.config import AuthSettings, Settings
from src.db.interfaces.postgresql import Base
from src.dependencies import get_db_session, get_settings
from src.models import KnowledgeBase
from src.repositories import GrantRepository
from src.routers import knowledge_bases
from src.services.access import RetrievalScope, resolve_scope


def _settings(**auth) -> Settings:
    return Settings(auth=AuthSettings(**{"mode": "jwt", **auth}))


def _user(subject="bob", groups=(), raw_roles=(), roles=(Role.QUERIER,), method="jwt") -> Principal:
    return Principal(
        subject=subject,
        roles=frozenset(roles),
        groups=frozenset(groups),
        raw_roles=frozenset(raw_roles),
        auth_method=method,
    )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def kbs(session_factory):
    """Finance (group finance), Public (everyone), Ungranted (no grants)."""
    with session_factory() as session:
        created = {name: KnowledgeBase(name=name, slug=name.lower(), index_name=f"idx-{name.lower()}") for name in ("Finance", "Public", "Ungranted")}
        session.add_all(created.values())
        session.commit()
        repo = GrantRepository(session)
        repo.replace_for_knowledge_base(created["Finance"].id, [("group", "finance")])
        repo.replace_for_knowledge_base(created["Public"].id, [("everyone", "*")])
        return {name: str(kb.id) for name, kb in created.items()}


def _resolve(session_factory, principal, settings):
    with session_factory() as session:
        return resolve_scope(principal, session, settings)


class TestResolveScope:
    def test_disabled_mode_is_unrestricted_for_everyone(self, session_factory, kbs):
        settings = Settings(auth=AuthSettings(mode="disabled"))
        for principal in (Principal.anonymous(), _user(method="api_key")):
            assert _resolve(session_factory, principal, settings).is_unrestricted

    def test_grants_decide_for_users(self, session_factory, kbs):
        scope = _resolve(session_factory, _user(groups=["finance"]), _settings())
        assert scope.kb_ids == {kbs["Finance"], kbs["Public"]}
        assert scope.reason == "grants"

    def test_user_without_matching_grant_sees_only_everyone_grants(self, session_factory, kbs):
        assert _resolve(session_factory, _user(), _settings()).kb_ids == {kbs["Public"]}

    def test_ungranted_allow_adds_knowledge_bases_without_grants(self, session_factory, kbs):
        scope = _resolve(session_factory, _user(), _settings(ungranted_kb_access="allow"))
        assert scope.kb_ids == {kbs["Public"], kbs["Ungranted"]}

    def test_admin_is_bound_by_grants_by_default(self, session_factory, kbs):
        admin = _user(roles=(Role.ADMIN, Role.QUERIER))
        assert _resolve(session_factory, admin, _settings()).kb_ids == {kbs["Public"]}

    def test_admin_bypass(self, session_factory, kbs):
        admin = _user(roles=(Role.ADMIN, Role.QUERIER))
        scope = _resolve(session_factory, admin, _settings(admin_bypass_retrieval=True))
        assert scope.is_unrestricted and scope.reason == "admin bypass"

    def test_bypass_does_not_apply_to_non_admins(self, session_factory, kbs):
        scope = _resolve(session_factory, _user(), _settings(admin_bypass_retrieval=True))
        assert not scope.is_unrestricted

    def test_application_key_alone_is_limited_by_the_application(self, session_factory, kbs):
        scope = _resolve(session_factory, _user(method="api_key"), _settings())
        assert scope.is_unrestricted and scope.reason == "application API key"

    def test_no_grants_anywhere_means_nothing(self, session_factory):
        assert _resolve(session_factory, _user(), _settings()).kb_ids == frozenset()


class TestRestrict:
    scope = RetrievalScope(kb_ids=frozenset({"b"}), reason="grants")

    def test_intersection_and_dropped(self):
        decision = self.scope.restrict(["a", "b", "c", "b"])
        assert decision.permitted == ["b"]
        assert decision.dropped == ["a", "c"]
        assert decision.requested == ["a", "b", "c"]

    def test_nothing_permitted_is_an_empty_list_not_none(self):
        assert self.scope.restrict(["a"]).permitted == []

    def test_global_request_becomes_the_permitted_set(self):
        assert self.scope.restrict(None).permitted == ["b"]
        assert RetrievalScope(kb_ids=frozenset(), reason="grants").restrict(None).permitted == []

    def test_unrestricted_passes_requests_through(self):
        unrestricted = RetrievalScope.unrestricted("x")
        assert unrestricted.restrict(None).permitted is None
        assert unrestricted.restrict(["a", "b"]).permitted == ["a", "b"]
        assert unrestricted.restrict(["a"]).dropped == []

    def test_uuid_inputs_are_normalised(self):
        import uuid

        kb = uuid.uuid4()
        assert RetrievalScope(kb_ids=frozenset({str(kb)}), reason="g").restrict([kb]).permitted == [str(kb)]


class TestCacheToken:
    def test_unrestricted_has_no_token(self):
        assert RetrievalScope.unrestricted("x").cache_token() is None

    def test_token_is_order_independent_and_distinguishes_scopes(self):
        a = RetrievalScope(kb_ids=frozenset({"1", "2"}), reason="g").cache_token()
        b = RetrievalScope(kb_ids=frozenset({"2", "1"}), reason="g").cache_token()
        c = RetrievalScope(kb_ids=frozenset({"1"}), reason="g").cache_token()
        empty = RetrievalScope(kb_ids=frozenset(), reason="g").cache_token()
        assert a == b and a != c and empty not in (a, c, None)


class TestAccessCheckEndpoint:
    @pytest.fixture
    def client(self, session_factory, kbs):
        app = FastAPI()
        app.include_router(knowledge_bases.router)

        def _session():
            session = session_factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db_session] = _session
        app.dependency_overrides[get_settings] = lambda: _settings()
        return TestClient(app)

    def test_preview_for_group_member(self, client):
        body = client.get("/api/v1/knowledge-bases/access-check", params={"subject": "bob", "groups": "finance"}).json()
        assert body["unrestricted"] is False
        assert {kb["name"] for kb in body["knowledge_bases"]} == {"Finance", "Public"}

    def test_preview_for_outsider(self, client):
        body = client.get("/api/v1/knowledge-bases/access-check", params={"subject": "eve"}).json()
        assert [kb["name"] for kb in body["knowledge_bases"]] == ["Public"]

    def test_subject_required(self, client):
        assert client.get("/api/v1/knowledge-bases/access-check").status_code == 422

    def test_literal_path_is_not_captured_as_an_id(self, client):
        response = client.get("/api/v1/knowledge-bases/access-check", params={"subject": "x"})
        assert response.status_code == 200
