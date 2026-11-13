"""API tests for the Admin Control Plane domain routers.

These run fully offline against an in-memory SQLite database (enabled by the
portable ``GUID`` column type), so no PostgreSQL/OpenSearch/Redis is required.
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
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


# --------------------------------------------------------------------------- #
# Per-application inference endpoints
# --------------------------------------------------------------------------- #
@pytest.fixture
def endpoint_providers(app, rag_mocks):
    """A real registry whose builder returns one distinct fake provider per endpoint."""
    from src.dependencies import get_provider_registry
    from src.services.providers.llm.registry import ProviderRegistry

    built = []

    def builder(provider_type, endpoint, api_key, model):
        provider = AsyncMock()
        provider.endpoint, provider.api_key, provider.provider_type = endpoint, api_key, provider_type
        provider.generate_rag_answer = AsyncMock(return_value={"answer": f"answered by {endpoint}", "sources": []})

        async def stream(**kwargs):
            yield {"response": f"streamed by {endpoint}"}

        provider.generate_rag_answer_stream = stream
        built.append(provider)
        return provider

    registry = ProviderRegistry(builder=builder)
    app.dependency_overrides[get_provider_registry] = lambda: registry
    return built


def _app_on_endpoint(client, name, endpoint=None, **extra):
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": f"{name} KB"}).json()["id"]
    payload = {"name": name, "llm_endpoint": endpoint, "knowledge_base_ids": [kb_id], **extra}
    resp = client.post("/api/v1/applications", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ask(client, application):
    resp = client.post(f"/api/v1/applications/{application['id']}/ask", json={"query": "How much leave?"})
    return resp


def _complete(client, application):
    return client.post(
        "/v1/chat/completions",
        json={"model": application["slug"], "messages": [{"role": "user", "content": "How much leave?"}]},
    )


def test_applications_are_answered_by_their_own_endpoint(client, rag_mocks, endpoint_providers):
    _, _, global_llm = rag_mocks
    gpu_a = _app_on_endpoint(client, "GPU A", "http://gpu-a:8000/v1", llm_provider="openai-compatible")
    gpu_b = _app_on_endpoint(client, "GPU B", "http://gpu-b:8000/v1", llm_provider="openai-compatible")
    plain = _app_on_endpoint(client, "Plain")

    assert _ask(client, gpu_a).json()["answer"] == "answered by http://gpu-a:8000/v1"
    assert _ask(client, gpu_b).json()["answer"] == "answered by http://gpu-b:8000/v1"
    assert _ask(client, plain).json()["answer"] == "25 days."  # the global provider, as today

    # The OpenAI-compatible path routes the same way.
    assert _complete(client, gpu_a).json()["choices"][0]["message"]["content"] == "answered by http://gpu-a:8000/v1"
    assert _complete(client, gpu_b).json()["choices"][0]["message"]["content"] == "answered by http://gpu-b:8000/v1"
    assert _complete(client, plain).json()["choices"][0]["message"]["content"] == "25 days."

    assert {p.endpoint for p in endpoint_providers} == {"http://gpu-a:8000/v1", "http://gpu-b:8000/v1"}
    assert global_llm.generate_rag_answer.await_count == 2  # only the two plain requests


def test_streaming_uses_the_applications_endpoint(client, rag_mocks, endpoint_providers):
    gpu = _app_on_endpoint(client, "Streamer", "http://gpu-a:8000/v1", llm_provider="openai-compatible")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": gpu["slug"], "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as resp:
        raw = "".join(resp.iter_text())
    assert "streamed by http://gpu-a:8000/v1" in raw


def test_one_provider_is_built_per_endpoint_and_key(client, endpoint_providers, monkeypatch):
    monkeypatch.setenv("GPU_KEY", "first")
    a = _app_on_endpoint(client, "Cache A", "http://gpu:8000/v1", llm_provider="openai-compatible", llm_api_key_ref="env:GPU_KEY")
    b = _app_on_endpoint(client, "Cache B", "http://gpu:8000/v1", llm_provider="openai-compatible", llm_api_key_ref="env:GPU_KEY")
    _ask(client, a)
    _ask(client, b)
    assert len(endpoint_providers) == 1

    monkeypatch.setenv("GPU_KEY", "rotated")  # read at use time, so a rotated key is a new provider
    _ask(client, a)
    assert [p.api_key for p in endpoint_providers] == ["first", "rotated"]


def test_key_references_resolve_from_the_environment_and_literals_pass_through(client, endpoint_providers, monkeypatch):
    monkeypatch.setenv("GPU_KEY", "from-env")
    referenced = _app_on_endpoint(client, "Ref", "http://gpu-a/v1", llm_provider="openai-compatible", llm_api_key_ref="env:GPU_KEY")
    literal = _app_on_endpoint(client, "Lit", "http://gpu-b/v1", llm_provider="openai-compatible", llm_api_key_ref="sk-plain")
    _ask(client, referenced)
    _ask(client, literal)
    assert {p.endpoint: p.api_key for p in endpoint_providers} == {"http://gpu-a/v1": "from-env", "http://gpu-b/v1": "sk-plain"}


def test_a_missing_key_variable_is_named_in_the_error(client, endpoint_providers, monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    application = _app_on_endpoint(
        client, "No Key", "http://gpu/v1", llm_provider="openai-compatible", llm_api_key_ref="env:NOT_SET_ANYWHERE"
    )

    playground = _ask(client, application)
    assert playground.status_code == 500
    assert "NOT_SET_ANYWHERE" in playground.json()["detail"]

    completion = _complete(client, application)
    assert completion.status_code == 500
    assert "NOT_SET_ANYWHERE" in completion.json()["error"]["message"]
    assert endpoint_providers == []


def test_the_endpoint_can_be_set_later_and_literal_keys_are_never_returned(client, endpoint_providers):
    application = _app_on_endpoint(client, "Later")
    patched = client.patch(
        f"/api/v1/applications/{application['id']}",
        json={"llm_provider": "openai-compatible", "llm_endpoint": "http://new-gpu/v1", "llm_api_key_ref": "sk-secret"},
    )
    assert patched.status_code == 200, patched.text
    config = patched.json()["model_configuration"]
    assert config["llm_endpoint"] == "http://new-gpu/v1"
    assert "sk-secret" not in patched.text
    assert _ask(client, application).json()["answer"] == "answered by http://new-gpu/v1"


# --------------------------------------------------------------------------- #
# Endpoint host allowlist
# --------------------------------------------------------------------------- #
@pytest.fixture
def allow_hosts(app):
    from src.config import LLMSettings, Settings

    def _set(hosts: str):
        app.dependency_overrides[get_settings] = lambda: Settings(llm=LLMSettings(allowed_endpoint_hosts=hosts))

    return _set


def test_an_empty_allowlist_allows_any_host(client, allow_hosts):
    allow_hosts("")
    resp = client.post("/api/v1/applications", json={"name": "Open", "llm_endpoint": "http://anywhere.example/v1"})
    assert resp.status_code == 201, resp.text


def test_saving_an_endpoint_on_an_unlisted_host_is_rejected(client, allow_hosts):
    allow_hosts("gpu-a.internal, GPU-B.internal:8000")

    ok = client.post("/api/v1/applications", json={"name": "Listed", "llm_endpoint": "http://gpu-a.internal:9000/v1"})
    assert ok.status_code == 201, ok.text
    listed_port = client.post("/api/v1/applications", json={"name": "Port", "llm_endpoint": "http://gpu-b.internal:8000/v1"})
    assert listed_port.status_code == 201, listed_port.text
    # Same host, port not listed.
    assert client.post("/api/v1/applications", json={"name": "Wrong port", "llm_endpoint": "http://gpu-b.internal:9/v1"}).status_code == 400

    rejected = client.post("/api/v1/applications", json={"name": "Evil", "llm_endpoint": "http://169.254.169.254/latest"})
    assert rejected.status_code == 400
    assert "169.254.169.254" in rejected.json()["detail"]
    assert "LLM__ALLOWED_ENDPOINT_HOSTS" in rejected.json()["detail"]

    # An endpoint on a lookalike host is not a match.
    sneaky = client.post("/api/v1/applications", json={"name": "Sneaky", "llm_endpoint": "http://gpu-a.internal.evil.example/v1"})
    assert sneaky.status_code == 400

    # Nothing from the rejected requests was saved.
    assert {a["name"] for a in client.get("/api/v1/applications").json()} == {"Listed", "Port"}


def test_updating_an_endpoint_to_an_unlisted_host_is_rejected(client, allow_hosts):
    allow_hosts("gpu-a.internal")
    application = client.post("/api/v1/applications", json={"name": "Upd", "llm_endpoint": "http://gpu-a.internal/v1"}).json()
    resp = client.patch(f"/api/v1/applications/{application['id']}", json={"llm_endpoint": "http://elsewhere/v1"})
    assert resp.status_code == 400
    assert client.get(f"/api/v1/applications/{application['id']}").json()["model_configuration"]["llm_endpoint"] == (
        "http://gpu-a.internal/v1"
    )


def test_a_saved_endpoint_is_refused_at_use_once_it_is_no_longer_listed(client, rag_mocks):
    from src.dependencies import get_provider_registry
    from src.services.providers.llm.registry import ProviderRegistry

    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "KB"}).json()["id"]
    application = client.post(
        "/api/v1/applications",
        json={"name": "Stale", "llm_endpoint": "http://old-gpu/v1", "knowledge_base_ids": [kb_id]},
    ).json()
    app_ = client.app
    app_.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry(
        builder=lambda *a: AsyncMock(), allowed_hosts=["new-gpu"]
    )

    resp = client.post(f"/api/v1/applications/{application['id']}/ask", json={"query": "q"})
    assert resp.status_code == 500
    assert "old-gpu" in resp.json()["detail"]
    completion = client.post(
        "/v1/chat/completions", json={"model": application["slug"], "messages": [{"role": "user", "content": "q"}]}
    )
    assert completion.status_code == 500 and "old-gpu" in completion.json()["error"]["message"]


def _provider_row(client, endpoint):
    resp = client.post(
        "/api/v1/providers",
        json={"name": "p", "kind": "llm", "provider_type": "openai-compatible", "endpoint": endpoint, "model": "m"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_provider_test_and_model_listing_obey_the_allowlist(client, allow_hosts, monkeypatch):
    allow_hosts("gpu-a.internal")
    blocked = _provider_row(client, "http://169.254.169.254/v1")

    test = client.post(f"/api/v1/providers/{blocked}/test")
    models = client.get(f"/api/v1/providers/{blocked}/models")
    for resp in (test, models):
        assert resp.status_code == 400
        assert "169.254.169.254" in resp.json()["detail"]


def test_provider_actions_still_reach_a_listed_host(client, allow_hosts, monkeypatch):
    import src.routers.providers as providers_router

    allow_hosts("gpu-a.internal")
    fake = AsyncMock()
    fake.list_models = AsyncMock(return_value=[{"id": "qwen"}])
    monkeypatch.setattr(providers_router, "_llm_client_for_provider", lambda provider: fake)
    listed = _provider_row(client, "http://gpu-a.internal/v1")

    resp = client.get(f"/api/v1/providers/{listed}/models")
    assert resp.status_code == 200
    assert resp.json()["reachable"] is True


# --------------------------------------------------------------------------- #
# Ingestion hardening
# --------------------------------------------------------------------------- #
@pytest.fixture
def uploads(client, app, session_factory, tmp_path):
    """An upload endpoint wired to a temp upload directory and a configurable size cap."""
    from src.config import IngestionSettings, Settings

    ingestion = MagicMock()
    ingestion.ingest_document = AsyncMock(return_value={})
    database = MagicMock()
    database.get_session.return_value.__enter__ = lambda *a: session_factory()
    database.get_session.return_value.__exit__ = lambda *a: None
    app.dependency_overrides[get_ingestion_service] = lambda: ingestion
    app.dependency_overrides[get_database] = lambda: database
    kb_id = client.post("/api/v1/knowledge-bases", json={"name": "Uploads KB"}).json()["id"]

    def configure(max_upload_mb=50):
        settings = Settings(ingestion=IngestionSettings(max_upload_mb=max_upload_mb, upload_dir=str(tmp_path / "uploads")))
        app.dependency_overrides[get_settings] = lambda: settings

    configure()
    return SimpleNamespace(kb_id=kb_id, configure=configure, root=tmp_path / "uploads")


def _stored_documents(session_factory):
    from src.models.document import Document

    with session_factory() as session:
        return list(session.query(Document))


def test_an_upload_over_the_size_cap_is_refused_and_nothing_is_stored(client, uploads, session_factory):
    uploads.configure(max_upload_mb=1)
    resp = client.post(
        f"/api/v1/knowledge-bases/{uploads.kb_id}/documents",
        files={"file": ("big.pdf", b"0" * (1024 * 1024 + 1), "application/pdf")},
    )
    assert resp.status_code == 413
    assert "1 MB" in resp.json()["detail"]
    assert _stored_documents(session_factory) == []
    assert not uploads.root.exists() or not any(uploads.root.rglob("*"))


def test_an_upload_at_the_cap_is_accepted(client, uploads):
    uploads.configure(max_upload_mb=1)
    resp = client.post(
        f"/api/v1/knowledge-bases/{uploads.kb_id}/documents",
        files={"file": ("exact.txt", b"0" * (1024 * 1024), "text/plain")},
    )
    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd.pdf", "..\\..\\windows\\evil.pdf", "/abs/path/x.pdf", "a/../../b.pdf", "evil\x00.pdf", "<script>\n.pdf"],
)
def test_stored_filenames_cannot_reach_outside_the_upload_area(client, uploads, session_factory, hostile):
    resp = client.post(
        f"/api/v1/knowledge-bases/{uploads.kb_id}/documents",
        files={"file": (hostile, b"%PDF-1.4 content", "application/pdf")},
    )
    assert resp.status_code == 202, resp.text

    [document] = _stored_documents(session_factory)
    stored = Path(document.file_path).resolve()
    assert stored.is_relative_to(uploads.root.resolve())
    assert stored.parent.name == uploads.kb_id and stored.exists()
    # The stored name is the content hash plus a safe extension: nothing from the supplied name.
    assert stored.name == f"{document.checksum}.pdf"
    assert "/" not in document.source_uri and "\\" not in document.source_uri and "\x00" not in document.source_uri
    assert not list(uploads.root.parent.glob("**/passwd*")) and not list(uploads.root.parent.glob("**/evil*"))


def test_an_odd_extension_is_dropped_not_trusted(client, uploads, session_factory):
    client.post(
        f"/api/v1/knowledge-bases/{uploads.kb_id}/documents",
        files={"file": ("report.p;df$(rm)", b"%PDF-1.4", "application/pdf")},
    )
    [document] = _stored_documents(session_factory)
    assert Path(document.file_path).name == document.checksum  # no usable extension, so none


def test_a_reprocess_after_cleanup_says_the_file_is_gone(client, uploads, session_factory):
    from src.models.document import Document

    client.post(
        f"/api/v1/knowledge-bases/{uploads.kb_id}/documents",
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
    )
    [document] = _stored_documents(session_factory)
    with session_factory() as session:
        stored = session.get(Document, document.id)
        stored.file_path = None
        session.commit()

    resp = client.post(f"/api/v1/documents/{document.id}/reprocess")
    assert resp.status_code == 409
    assert "cleanup" in resp.json()["detail"].lower()
