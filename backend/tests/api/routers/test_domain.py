"""API tests for the Admin Control Plane domain routers.

These run fully offline against an in-memory SQLite database (enabled by the
portable ``GUID`` column type), so no PostgreSQL/OpenSearch/Redis is required.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.db.interfaces.postgresql import Base
from src.dependencies import (
    get_database,
    get_db_session,
    get_embeddings_service,
    get_ingestion_service,
    get_llm_provider,
    get_opensearch_client,
    get_settings,
)
from src.routers import (
    api_keys,
    applications,
    chat_completions,
    documents,
    knowledge_bases,
    providers,
    retrieval,
    system,
)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def app(session_factory):
    application = FastAPI()
    for module in (
        knowledge_bases,
        documents,
        applications,
        providers,
        retrieval,
        system,
        api_keys,
        chat_completions,
    ):
        application.include_router(module.router)

    def _override_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db_session] = _override_session
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Knowledge bases
# --------------------------------------------------------------------------- #
def test_knowledge_base_crud(client):
    resp = client.post("/api/v1/knowledge-bases", json={"name": "HR Policies", "description": "docs"})
    assert resp.status_code == 201, resp.text
    kb = resp.json()
    assert kb["slug"] == "hr-policies"
    assert kb["index_name"].endswith("hr-policies-chunks")
    kb_id = kb["id"]

    # duplicate name rejected
    assert client.post("/api/v1/knowledge-bases", json={"name": "HR Policies"}).status_code == 409

    # list
    listed = client.get("/api/v1/knowledge-bases").json()
    assert len(listed) == 1

    # get
    assert client.get(f"/api/v1/knowledge-bases/{kb_id}").json()["name"] == "HR Policies"

    # update
    updated = client.patch(f"/api/v1/knowledge-bases/{kb_id}", json={"default_top_k": 5})
    assert updated.json()["default_top_k"] == 5

    # delete
    assert client.delete(f"/api/v1/knowledge-bases/{kb_id}").status_code == 204
    assert client.get(f"/api/v1/knowledge-bases/{kb_id}").status_code == 404


def test_knowledge_base_not_found(client):
    assert client.get(f"/api/v1/knowledge-bases/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def test_provider_crud(client):
    payload = {
        "name": "Local Ollama",
        "kind": "llm",
        "provider_type": "ollama",
        "endpoint": "http://ollama:11434",
        "model": "qwen2.5:7b",
    }
    resp = client.post("/api/v1/providers", json=payload)
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]

    assert len(client.get("/api/v1/providers").json()) == 1
    assert len(client.get("/api/v1/providers?kind=embedding").json()) == 0

    updated = client.patch(f"/api/v1/providers/{pid}", json={"model": "qwen2.5:14b"})
    assert updated.json()["model"] == "qwen2.5:14b"

    assert client.delete(f"/api/v1/providers/{pid}").status_code == 204


# --------------------------------------------------------------------------- #
# Retrieval configurations
# --------------------------------------------------------------------------- #
def test_retrieval_config_crud(client):
    resp = client.post(
        "/api/v1/retrieval-configurations",
        json={"name": "hybrid-default", "mode": "hybrid", "top_k": 8},
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    assert resp.json()["mode"] == "hybrid"
    assert client.get(f"/api/v1/retrieval-configurations/{cid}").status_code == 200
    assert client.delete(f"/api/v1/retrieval-configurations/{cid}").status_code == 204


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #
def test_application_crud_with_knowledge_base(client):
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "Handbook"}).json()["id"]

    payload = {
        "name": "HR Assistant",
        "description": "Answers HR questions",
        "knowledge_base_ids": [kb_id],
        "llm_model": "qwen2.5:7b",
        "retrieval_mode": "hybrid",
        "top_k": 8,
        "system_prompt": "You are helpful.",
    }
    resp = client.post("/api/v1/applications", json=payload)
    assert resp.status_code == 201, resp.text
    app_json = resp.json()
    assert app_json["slug"] == "hr-assistant"
    # Configuration is persisted, not ignored.
    assert app_json["model_configuration"]["llm_model"] == "qwen2.5:7b"
    assert app_json["retrieval_configuration"]["top_k"] == 8
    assert len(app_json["knowledge_bases"]) == 1

    app_id = app_json["id"]
    updated = client.patch(f"/api/v1/applications/{app_id}", json={"description": "updated"})
    assert updated.json()["description"] == "updated"

    assert client.delete(f"/api/v1/applications/{app_id}").status_code == 204


def test_application_ask_respects_configuration(client, app, session_factory):
    """The ask endpoint must honour the stored top_k / model / mode."""
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "KB"}).json()["id"]
    app_id = client.post(
        "/api/v1/applications",
        json={
            "name": "Bot",
            "knowledge_base_ids": [kb_id],
            "llm_model": "qwen2.5:7b",
            "retrieval_mode": "hybrid",
            "top_k": 8,
        },
    ).json()["id"]

    mock_os = MagicMock()
    mock_os.search_unified = MagicMock(
        return_value={"hits": [{"chunk_text": "Leave is 25 days.", "title": "Handbook", "score": 0.9, "document_id": "d1"}]}
    )
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.1] * 1024)
    mock_llm = AsyncMock()
    mock_llm.generate_rag_answer = AsyncMock(return_value={"answer": "25 days of leave.", "sources": []})

    app.dependency_overrides[get_opensearch_client] = lambda: mock_os
    app.dependency_overrides[get_embeddings_service] = lambda: mock_embeddings
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm

    resp = client.post(f"/api/v1/applications/{app_id}/ask", json={"query": "How much leave?"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == "25 days of leave."
    assert data["search_mode"] == "hybrid"
    assert data["chunks_used"] == 1
    assert data["sources"][0]["document_title"] == "Handbook"

    # Configured top_k=8 and model must be passed through.
    assert mock_os.search_unified.call_args.kwargs["size"] == 8
    assert mock_llm.generate_rag_answer.call_args.kwargs["model"] == "qwen2.5:7b"


# --------------------------------------------------------------------------- #
# System stats
# --------------------------------------------------------------------------- #
def test_system_stats(client):
    client.post("/api/v1/knowledge-bases", json={"name": "KB one"})
    client.post("/api/v1/knowledge-bases", json={"name": "KB two"})
    stats = client.get("/api/v1/system/stats").json()
    assert stats["knowledge_bases"] == 2
    assert stats["documents"] == 0
    assert "QUEUED" in stats["ingestion_jobs_by_status"]


def test_gpu_endpoint_never_fabricates(client):
    data = client.get("/api/v1/system/gpu").json()
    assert set(["available", "gpus"]).issubset(data.keys())
    assert isinstance(data["available"], bool)


# --------------------------------------------------------------------------- #
# Document upload + ingestion wiring
# --------------------------------------------------------------------------- #
def test_document_upload_queues_ingestion(client, app, session_factory):
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "Docs KB"}).json()["id"]

    mock_ingestion = MagicMock()
    mock_ingestion.ingest_document = AsyncMock(return_value={"chunks_indexed": 1})

    # Provide a database whose sessions come from the same in-memory engine so
    # the background task can load the persisted document/job.
    mock_db = MagicMock()
    mock_db.get_session.return_value.__enter__ = lambda *a: session_factory()
    mock_db.get_session.return_value.__exit__ = lambda *a: None

    app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion
    app.dependency_overrides[get_database] = lambda: mock_db
    app.dependency_overrides[get_opensearch_client] = lambda: MagicMock()
    app.dependency_overrides[get_embeddings_service] = lambda: MagicMock()
    app.dependency_overrides[get_settings] = get_settings

    resp = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        files={"file": ("policy.txt", b"Annual leave is 25 days per year.", "text/plain")},
        data={"title": "Leave Policy"},
    )
    assert resp.status_code == 202, resp.text
    doc = resp.json()
    assert doc["title"] == "Leave Policy"
    assert doc["status"] == "QUEUED"
    assert doc["content_type"] == "text/plain"

    # Document + ingestion job are persisted and listable.
    docs = client.get(f"/api/v1/knowledge-bases/{kb_id}/documents").json()
    assert len(docs) == 1
    jobs = client.get("/api/v1/ingestion-jobs").json()
    assert len(jobs) == 1
    # Background ingestion was scheduled and invoked.
    mock_ingestion.ingest_document.assert_called_once()


def test_document_upload_rejects_duplicate(client, app, session_factory):
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "Dup KB"}).json()["id"]
    mock_ingestion = MagicMock()
    mock_ingestion.ingest_document = AsyncMock(return_value={})
    mock_db = MagicMock()
    mock_db.get_session.return_value.__enter__ = lambda *a: session_factory()
    mock_db.get_session.return_value.__exit__ = lambda *a: None
    app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion
    app.dependency_overrides[get_database] = lambda: mock_db

    files = {"file": ("a.txt", b"identical bytes", "text/plain")}
    assert (
        client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents", files={"file": ("a.txt", b"identical bytes", "text/plain")}
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents", files={"file": ("a.txt", b"identical bytes", "text/plain")}
        ).status_code
        == 409
    )


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
def _make_app(client, name="Keyed Bot", kb_ids=None):
    if kb_ids is None:
        kb_ids = [client.post("/api/v1/knowledge-bases", json={"name": f"{name} KB"}).json()["id"]]
    return client.post(
        "/api/v1/applications",
        json={"name": name, "knowledge_base_ids": kb_ids, "llm_model": "qwen2.5:7b"},
    ).json()


def test_application_create_auto_provisions_key(client):
    """Creation hands back a usable key exactly once."""
    created = _make_app(client)
    assert created["api_key"].startswith("sk-")

    # ...and never again on reads.
    fetched = client.get(f"/api/v1/applications/{created['id']}").json()
    assert fetched["api_key"] is None

    listed = client.get("/api/v1/applications").json()
    assert all(a["api_key"] is None for a in listed)


def test_api_key_lifecycle(client):
    app_id = _make_app(client)["id"]

    keys = client.get(f"/api/v1/applications/{app_id}/api-keys").json()
    assert len(keys) == 1, "creation should have provisioned a default key"
    assert "key" not in keys[0], "the plaintext must never be listed"
    assert keys[0]["is_active"] is True

    created = client.post(f"/api/v1/applications/{app_id}/api-keys", json={"name": "CI"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"].startswith("sk-")
    assert body["key"].endswith(body["key_last4"])
    assert body["key"].startswith(body["key_prefix"])

    rotated = client.post(f"/api/v1/applications/{app_id}/api-keys/{body['id']}/rotate")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["key"] != body["key"]

    after_rotate = {k["id"]: k for k in client.get(f"/api/v1/applications/{app_id}/api-keys").json()}
    assert after_rotate[body["id"]]["is_active"] is False, "rotate must revoke its predecessor"

    default_key_id = keys[0]["id"]
    assert client.delete(f"/api/v1/applications/{app_id}/api-keys/{default_key_id}").status_code == 204
    after_revoke = {k["id"]: k for k in client.get(f"/api/v1/applications/{app_id}/api-keys").json()}
    assert after_revoke[default_key_id]["is_active"] is False
    assert after_revoke[default_key_id]["revoked_at"] is not None


def test_api_keys_scoped_to_application(client):
    a = _make_app(client, name="App A")
    b = _make_app(client, name="App B")
    b_key_id = client.get(f"/api/v1/applications/{b['id']}/api-keys").json()[0]["id"]
    # A key belonging to B is not reachable through A.
    assert client.delete(f"/api/v1/applications/{a['id']}/api-keys/{b_key_id}").status_code == 404


# --------------------------------------------------------------------------- #
# OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture
def rag_mocks(app):
    """Override the retrieval/generation collaborators with recording mocks."""
    mock_os = MagicMock()
    mock_os.search_unified = MagicMock(
        return_value={"hits": [{"chunk_text": "Leave is 25 days.", "title": "Handbook", "score": 0.9, "document_id": "d1"}]}
    )
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.1] * 1024)
    mock_llm = AsyncMock()
    mock_llm.generate_rag_answer = AsyncMock(return_value={"answer": "25 days.", "sources": []})

    app.dependency_overrides[get_opensearch_client] = lambda: mock_os
    app.dependency_overrides[get_embeddings_service] = lambda: mock_embeddings
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    return mock_os, mock_embeddings, mock_llm


def test_chat_completion_resolves_application_by_slug(client, rag_mocks):
    created = _make_app(client, name="HR Assistant")
    assert created["slug"] == "hr-assistant"

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "hr-assistant", "messages": [{"role": "user", "content": "How much leave?"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "hr-assistant"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "25 days."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["id"].startswith("chatcmpl-")


def test_chat_completion_unknown_model_uses_openai_error_shape(client, rag_mocks):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "does-not-exist", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404
    # The OpenAI SDKs render {"error": {...}}; FastAPI's default {"detail": ...}
    # would surface as an opaque failure.
    assert resp.json()["error"]["code"] == "model_not_found"


def test_chat_completion_does_not_enforce_api_keys(client, rag_mocks):
    """Keys are issued but NOT enforced -- this documents that decision.

    If enforcement is ever added, this test should be changed deliberately
    rather than discovered to be failing.
    """
    _make_app(client, name="Open Bot")
    payload = {"model": "open-bot", "messages": [{"role": "user", "content": "hi"}]}

    assert client.post("/v1/chat/completions", json=payload).status_code == 200
    assert (
        client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer sk-not-a-real-key"}).status_code
        == 200
    )


def test_chat_completion_requires_a_user_message(client, rag_mocks):
    _make_app(client, name="Sys Only")
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "sys-only", "messages": [{"role": "system", "content": "be nice"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_user_message"


def test_chat_completion_ignores_unknown_sdk_fields(client, rag_mocks):
    """Stock SDK clients send extras (top_p, n, presence_penalty, ...)."""
    _make_app(client, name="Tolerant Bot")
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "tolerant-bot",
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.9,
            "n": 1,
            "presence_penalty": 0.0,
            "seed": 42,
        },
    )
    assert resp.status_code == 200, resp.text


def test_chat_completion_streams_sse(client, rag_mocks):
    _, _, mock_llm = rag_mocks

    async def _fake_stream(**kwargs):
        for piece in ["25 ", "days."]:
            yield {"response": piece}

    mock_llm.generate_rag_answer_stream = _fake_stream
    _make_app(client, name="Stream Bot")

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "stream-bot", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())

    assert raw.rstrip().endswith("data: [DONE]")
    payloads = [
        json.loads(line[len("data: ") :])
        for line in raw.splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    ]
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert "".join(p["choices"][0]["delta"].get("content", "") for p in payloads) == "25 days."
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
