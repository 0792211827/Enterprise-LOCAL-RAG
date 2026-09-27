"""Authorisation on /v1/chat/completions: API-key enforcement and end users.

Runs offline against in-memory SQLite, like test_domain.py. JWTs are signed
with a key generated per run and verified from a PEM file.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.auth import DisabledAuthenticator
from src.auth.authenticators import JwtAuthenticator
from src.config import ApiKeySettings, AuthSettings, Settings
from src.db.interfaces.postgresql import Base
from src.dependencies import get_db_session, get_embeddings_service, get_llm_provider, get_opensearch_client, get_settings
from src.models import ApplicationApiKey
from src.routers import applications, chat_completions, knowledge_bases

ISSUER, AUDIENCE = "https://sso.test", "rag-platform"


@pytest.fixture(scope="module")
def idp_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def make_client(session_factory, idp_key, tmp_path):
    """Build a client with the given key-enforcement flag and auth mode."""

    def _make(enforce: bool = False, auth: str = "disabled") -> TestClient:
        app = FastAPI()
        for module in (knowledge_bases, applications, chat_completions):
            app.include_router(module.router)

        def _session():
            session = session_factory()
            try:
                yield session
            finally:
                session.close()

        auth_settings = AuthSettings(mode="disabled")
        if auth == "jwt":
            pem = tmp_path / "idp.pem"
            pem.write_bytes(
                idp_key.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )
            auth_settings = AuthSettings(
                mode="jwt",
                issuer=ISSUER,
                audience=AUDIENCE,
                public_key_path=str(pem),
                query_roles="rag-user",
                admin_roles="rag-admin",
                auditor_roles="rag-auditor",
            )
            app.state.authenticator = JwtAuthenticator(auth_settings)
        else:
            app.state.authenticator = DisabledAuthenticator()
        settings = Settings(auth=auth_settings, api_keys=ApiKeySettings(enforce=enforce))

        os_mock = MagicMock()
        os_mock.search_unified = MagicMock(
            return_value={"hits": [{"chunk_text": "Leave is 25 days.", "title": "Handbook", "score": 0.9}]}
        )
        embeddings = AsyncMock()
        embeddings.embed_query = AsyncMock(return_value=[0.1] * 1024)
        llm = AsyncMock()
        llm.generate_rag_answer = AsyncMock(return_value={"answer": "25 days.", "sources": []})

        app.dependency_overrides[get_db_session] = _session
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_opensearch_client] = lambda: os_mock
        app.dependency_overrides[get_embeddings_service] = lambda: embeddings
        app.dependency_overrides[get_llm_provider] = lambda: llm
        return TestClient(app)

    return _make


def _new_app(client: TestClient, name: str) -> dict:
    """Create an application (setup goes through the admin API; auth is off for it)."""
    kb = client.post("/api/v1/knowledge-bases", json={"name": f"{name} KB"}).json()["id"]
    return client.post("/api/v1/applications", json={"name": name, "knowledge_base_ids": [kb]}).json()


def _payload(slug: str) -> dict:
    return {"model": slug, "messages": [{"role": "user", "content": "How much leave?"}]}


def _bearer(value: str) -> dict:
    return {"Authorization": f"Bearer {value}"}


def _token(idp_key, roles=("rag-user",), **claims) -> str:
    body = {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300, "roles": list(roles)}
    body.update(claims)
    return jwt.encode(body, idp_key, algorithm="RS256")


def _last_used(session_factory, key_prefix: str):
    with session_factory() as session:
        key = session.query(ApplicationApiKey).filter(ApplicationApiKey.key_prefix == key_prefix).one()
        return key.last_used_at


class TestEnforcementOn:
    def test_missing_key(self, make_client):
        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"

    def test_unknown_key(self, make_client):
        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer("sk-nope"))
        assert response.status_code == 401

    def test_valid_key_is_served_and_recorded(self, make_client, session_factory):
        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(app["api_key"]))
        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "25 days."
        assert _last_used(session_factory, app["api_key"][:11]) is not None

    def test_key_for_another_application_is_rejected(self, make_client, session_factory):
        client = make_client(enforce=True)
        a, b = _new_app(client, "App A"), _new_app(client, "App B")
        response = client.post("/v1/chat/completions", json=_payload(b["slug"]), headers=_bearer(a["api_key"]))
        assert response.status_code == 401
        assert "not valid for this model" in response.json()["error"]["message"]
        assert _last_used(session_factory, a["api_key"][:11]) is None

    def test_revoked_key(self, make_client, session_factory):
        from datetime import datetime, timezone

        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        with session_factory() as session:
            key = session.query(ApplicationApiKey).filter(ApplicationApiKey.key_prefix == app["api_key"][:11]).one()
            key.revoked_at = datetime.now(timezone.utc)
            session.commit()
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(app["api_key"]))
        assert response.status_code == 401

    def test_unknown_model_without_key_does_not_reveal_existence(self, make_client):
        client = make_client(enforce=True)
        response = client.post("/v1/chat/completions", json=_payload("no-such-app"))
        assert response.status_code == 401  # not 404: slugs are not enumerable without a key

    def test_unknown_model_with_valid_key_is_404(self, make_client):
        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        response = client.post("/v1/chat/completions", json=_payload("no-such-app"), headers=_bearer(app["api_key"]))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "model_not_found"


class TestEnforcementOff:
    def test_no_key_still_served(self, make_client):
        client = make_client(enforce=False)
        app = _new_app(client, "Open Bot")
        assert client.post("/v1/chat/completions", json=_payload(app["slug"])).status_code == 200

    def test_valid_key_recorded(self, make_client, session_factory):
        client = make_client(enforce=False)
        app = _new_app(client, "Open Bot")
        client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(app["api_key"]))
        assert _last_used(session_factory, app["api_key"][:11]) is not None

    def test_key_for_another_application_is_not_recorded(self, make_client, session_factory):
        client = make_client(enforce=False)
        a, b = _new_app(client, "App A"), _new_app(client, "App B")
        response = client.post("/v1/chat/completions", json=_payload(b["slug"]), headers=_bearer(a["api_key"]))
        assert response.status_code == 200
        assert _last_used(session_factory, a["api_key"][:11]) is None


class TestEndUsers:
    def test_jwt_mode_requires_credentials(self, make_client):
        client = make_client(auth="jwt")
        app = _new_app(client, "Secure Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]))
        assert response.status_code == 401

    def test_jwt_querier_is_served(self, make_client, idp_key):
        client = make_client(auth="jwt")
        app = _new_app(client, "Secure Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(_token(idp_key)))
        assert response.status_code == 200, response.text

    def test_jwt_without_querier_role_is_403(self, make_client, idp_key):
        client = make_client(auth="jwt")
        app = _new_app(client, "Secure Bot")
        token = _token(idp_key, roles=("rag-auditor",))
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(token))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_permissions"

    def test_jwt_mode_accepts_application_key(self, make_client):
        client = make_client(auth="jwt")
        app = _new_app(client, "Secure Bot")
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=_bearer(app["api_key"]))
        assert response.status_code == 200

    def test_invalid_on_behalf_of_token_is_rejected(self, make_client):
        client = make_client(auth="jwt")
        app = _new_app(client, "Secure Bot")
        headers = {**_bearer(app["api_key"]), "X-User-Authorization": "Bearer not-a-jwt"}
        response = client.post("/v1/chat/completions", json=_payload(app["slug"]), headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_user_token"


class TestResolvedPrincipal:
    """Which identity a request is attributed to (feeds scoping and audit)."""

    @pytest.fixture
    def resolve(self, make_client, session_factory):
        from src.auth.completions import authorize_completion
        from src.repositories import ApplicationRepository
        from starlette.requests import Request

        async def _resolve(client: TestClient, slug: str, headers: dict):
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                "app": client.app,
            }
            settings = client.app.dependency_overrides[get_settings]()
            with session_factory() as session:
                principal, _ = await authorize_completion(
                    Request(scope), session, settings, slug, lambda: ApplicationRepository(session).get_by_slug(slug)
                )
            return principal

        return _resolve

    async def test_key_alone_is_the_application(self, make_client, resolve):
        client = make_client(enforce=True)
        app = _new_app(client, "HR Bot")
        principal = await resolve(client, app["slug"], _bearer(app["api_key"]))
        assert principal.auth_method == "api_key"
        assert str(principal.application_id) == app["id"]
        assert principal.subject == f"api-key:{principal.api_key_id}"

    async def test_on_behalf_of_user_carries_the_key(self, make_client, resolve, idp_key):
        client = make_client(enforce=True, auth="jwt")
        app = _new_app(client, "HR Bot")
        token = _token(idp_key, groups=["finance"])
        principal = await resolve(client, app["slug"], {**_bearer(app["api_key"]), "X-User-Authorization": f"Bearer {token}"})
        assert principal.subject == "alice"
        assert principal.auth_method == "jwt"
        assert principal.groups == {"finance"}
        assert principal.api_key_id is not None
        assert str(principal.application_id) == app["id"]

    async def test_disabled_without_key_is_anonymous(self, make_client, resolve):
        client = make_client()
        app = _new_app(client, "HR Bot")
        principal = await resolve(client, app["slug"], {})
        assert principal.auth_method == "disabled"
