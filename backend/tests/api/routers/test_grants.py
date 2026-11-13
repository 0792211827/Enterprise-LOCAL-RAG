"""Knowledge-base grants: admin API and the readable-KB query."""

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.db.interfaces.postgresql import Base
from src.dependencies import get_db_session
from src.models import KnowledgeBaseGrant
from src.repositories import GrantRepository
from src.routers import knowledge_bases


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(knowledge_bases.router)

    def _session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _session
    return TestClient(app)


def _kb(client, name="Finance") -> str:
    return client.post("/api/v1/knowledge-bases", json={"name": name}).json()["id"]


class TestGrantsApi:
    def test_new_knowledge_base_has_no_grants(self, client):
        assert client.get(f"/api/v1/knowledge-bases/{_kb(client)}/grants").json() == []

    def test_put_replaces_the_whole_set(self, client):
        kb = _kb(client)
        first = client.put(
            f"/api/v1/knowledge-bases/{kb}/grants",
            json={"grants": [{"principal_type": "group", "principal_value": "analysts"}]},
        )
        assert first.status_code == 200, first.text
        second = client.put(
            f"/api/v1/knowledge-bases/{kb}/grants",
            json={
                "grants": [
                    {"principal_type": "group", "principal_value": "auditors"},
                    {"principal_type": "subject", "principal_value": " alice "},
                ]
            },
        ).json()
        assert {(g["principal_type"], g["principal_value"]) for g in second} == {
            ("group", "auditors"),
            ("subject", "alice"),
        }
        assert all(g["created_by"] == "api_user" for g in second)  # auth disabled -> anonymous admin

    def test_duplicates_collapse(self, client):
        kb = _kb(client)
        grants = [{"principal_type": "group", "principal_value": "analysts"}] * 3
        body = client.put(f"/api/v1/knowledge-bases/{kb}/grants", json={"grants": grants}).json()
        assert len(body) == 1

    def test_everyone_normalises_value(self, client):
        kb = _kb(client)
        body = client.put(
            f"/api/v1/knowledge-bases/{kb}/grants",
            json={"grants": [{"principal_type": "everyone", "principal_value": "ignored"}]},
        ).json()
        assert body[0]["principal_value"] == "*"

    def test_empty_value_rejected(self, client):
        kb = _kb(client)
        response = client.put(
            f"/api/v1/knowledge-bases/{kb}/grants",
            json={"grants": [{"principal_type": "group", "principal_value": "  "}]},
        )
        assert response.status_code == 422

    def test_unknown_type_rejected(self, client):
        response = client.put(
            f"/api/v1/knowledge-bases/{_kb(client)}/grants",
            json={"grants": [{"principal_type": "department", "principal_value": "x"}]},
        )
        assert response.status_code == 422

    def test_clearing_grants(self, client):
        kb = _kb(client)
        client.put(f"/api/v1/knowledge-bases/{kb}/grants", json={"grants": [{"principal_type": "everyone"}]})
        assert client.put(f"/api/v1/knowledge-bases/{kb}/grants", json={"grants": []}).json() == []

    def test_unknown_knowledge_base(self, client):
        missing = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/api/v1/knowledge-bases/{missing}/grants").status_code == 404
        assert client.put(f"/api/v1/knowledge-bases/{missing}/grants", json={"grants": []}).status_code == 404

    def test_deleting_knowledge_base_removes_its_grants(self, client, session_factory):
        kb = _kb(client)
        client.put(f"/api/v1/knowledge-bases/{kb}/grants", json={"grants": [{"principal_type": "everyone"}]})
        assert client.delete(f"/api/v1/knowledge-bases/{kb}").status_code == 204
        with session_factory() as session:
            assert session.query(KnowledgeBaseGrant).count() == 0


class TestReadableKnowledgeBases:
    @pytest.fixture
    def kbs(self, client):
        ids = {name: _kb(client, name) for name in ("Finance", "HR", "Public", "Personal", "Ungranted")}
        put = lambda name, grants: client.put(f"/api/v1/knowledge-bases/{ids[name]}/grants", json={"grants": grants})
        put("Finance", [{"principal_type": "group", "principal_value": "finance"}])
        put("HR", [{"principal_type": "role", "principal_value": "hr-staff"}])
        put("Public", [{"principal_type": "everyone"}])
        put("Personal", [{"principal_type": "subject", "principal_value": "alice"}])
        return ids

    def _readable(self, session_factory, subject="bob", groups=(), roles=()):
        with session_factory() as session:
            return GrantRepository(session).readable_knowledge_base_ids(subject, groups, roles)

    def test_everyone_only(self, kbs, session_factory):
        assert self._readable(session_factory) == {kbs["Public"]}

    def test_group_match(self, kbs, session_factory):
        assert self._readable(session_factory, groups=["finance"]) == {kbs["Public"], kbs["Finance"]}

    def test_role_match(self, kbs, session_factory):
        assert self._readable(session_factory, roles=["hr-staff"]) == {kbs["Public"], kbs["HR"]}

    def test_subject_match(self, kbs, session_factory):
        assert self._readable(session_factory, subject="alice") == {kbs["Public"], kbs["Personal"]}

    def test_group_value_does_not_match_role_grant(self, kbs, session_factory):
        # A group literally named "hr-staff" is not the hr-staff role.
        assert kbs["HR"] not in self._readable(session_factory, groups=["hr-staff"])

    def test_granted_ids_excludes_ungranted(self, kbs, session_factory):
        with session_factory() as session:
            granted = GrantRepository(session).granted_knowledge_base_ids()
        assert kbs["Ungranted"] not in granted
        assert len(granted) == 4
