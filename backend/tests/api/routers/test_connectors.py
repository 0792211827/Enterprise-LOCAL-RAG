"""Connector definitions: an admin maps a mounted folder to one Knowledge Base. Running is a separate job (see
tests/unit/test_connector_sync.py), so this API only records definitions and reports how the last run went."""

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.auth import Principal, Role, get_principal, require_role
from src.config import AuthSettings, ConnectorSettings, Settings
from src.dependencies import get_db_session, get_settings
from src.models import Connector, ConnectorFileState, KnowledgeBase
from src.models.document import Document
from src.routers import connectors
from tests.audit_support import sqlite_session_factory

ADMIN = Principal(subject="root", roles=frozenset({Role.ADMIN, Role.QUERIER}), auth_method="jwt")
QUERIER = Principal(subject="alice", roles=frozenset({Role.QUERIER}), auth_method="jwt")


@pytest.fixture
def stack():
    factory = sqlite_session_factory()
    with factory() as session:
        kb = KnowledgeBase(name="hr", slug="hr", index_name="hr-idx")
        session.add(kb)
        session.commit()
        kb_id = str(kb.id)

    current = {"principal": ADMIN}
    settings = {"value": Settings(auth=AuthSettings(mode="jwt"), connector=ConnectorSettings(mount_root="/mnt/sources"))}
    app = FastAPI()
    app.include_router(connectors.router, dependencies=[Depends(require_role(Role.ADMIN))])

    def _session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings["value"]
    app.dependency_overrides[get_principal] = lambda: current["principal"]
    http = TestClient(app)
    return type(
        "Stack",
        (),
        {
            "http": http,
            "factory": factory,
            "kb_id": kb_id,
            "act_as": staticmethod(lambda p: current.update(principal=p)),
            "use_settings": staticmethod(lambda s: settings.update(value=s)),
        },
    )


def body(stack, **overrides):
    return {"name": "hr-share", "knowledge_base_id": stack.kb_id, "path": "/mnt/sources/hr", **overrides}


def test_an_admin_maps_a_folder_to_a_knowledge_base(stack):
    response = stack.http.post("/api/v1/connectors", json=body(stack))

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "hr-share" and created["knowledge_base_id"] == stack.kb_id
    assert created["source_type"] == "path" and created["source"] == {"path": "/mnt/sources/hr"}
    assert created["enabled"] is True and created["running"] is False and created["last_run_at"] is None
    assert (created["files_synced"], created["files_failed"]) == (0, 0)
    assert [c["id"] for c in stack.http.get("/api/v1/connectors").json()] == [created["id"]]
    assert stack.http.get(f"/api/v1/connectors/{created['id']}").json()["name"] == "hr-share"
    with stack.factory() as session:
        assert session.scalar(select(Connector)).created_by == "root"


def test_a_querier_cannot_define_list_change_or_delete_connectors(stack):
    created = stack.http.post("/api/v1/connectors", json=body(stack)).json()
    stack.act_as(QUERIER)

    assert stack.http.post("/api/v1/connectors", json=body(stack, name="other")).status_code == 403
    assert stack.http.get("/api/v1/connectors").status_code == 403
    assert stack.http.get(f"/api/v1/connectors/{created['id']}").status_code == 403
    assert stack.http.patch(f"/api/v1/connectors/{created['id']}", json={"enabled": False}).status_code == 403
    assert stack.http.delete(f"/api/v1/connectors/{created['id']}").status_code == 403
    with stack.factory() as session:
        assert session.scalar(select(Connector)).enabled is True


@pytest.mark.parametrize("path", ["/etc", "/mnt/sources/../etc", "relative", "/mnt/sources-evil"])
def test_a_path_outside_the_mount_root_is_refused(stack, path):
    response = stack.http.post("/api/v1/connectors", json=body(stack, path=path))

    assert response.status_code == 422
    assert "CONNECTOR__MOUNT_ROOT" in response.text or "absolute" in response.text
    with stack.factory() as session:
        assert session.scalar(select(Connector)) is None


def test_mounted_paths_are_refused_while_no_mount_root_is_configured(stack):
    stack.use_settings(Settings(auth=AuthSettings(mode="jwt")))

    response = stack.http.post("/api/v1/connectors", json=body(stack))

    assert response.status_code == 422 and "Mounted-path Connectors are off" in response.text


def test_an_unknown_knowledge_base_a_duplicate_name_and_a_missing_path_are_refused(stack):
    assert stack.http.post("/api/v1/connectors", json=body(stack, knowledge_base_id="00000000-0000-0000-0000-000000000000")).status_code == 404
    assert stack.http.post("/api/v1/connectors", json=body(stack)).status_code == 201
    assert stack.http.post("/api/v1/connectors", json=body(stack)).status_code == 409
    assert stack.http.post("/api/v1/connectors", json=body(stack, name="b", path="")).status_code == 422
    assert stack.http.post("/api/v1/connectors", json=body(stack, name="has space")).status_code == 422


def test_a_connector_can_be_paused_repointed_and_deleted_without_touching_its_documents(stack):
    created = stack.http.post("/api/v1/connectors", json=body(stack)).json()
    with stack.factory() as session:
        document = Document(knowledge_base_id=created["knowledge_base_id"], title="leave.txt", raw_text="x")
        session.add(document)
        session.commit()
        session.add(ConnectorFileState(connector_id=created["id"], source_key="leave.txt", document_id=document.id, status="synced"))
        session.commit()

    paused = stack.http.patch(f"/api/v1/connectors/{created['id']}", json={"enabled": False, "path": "/mnt/sources/finance"}).json()
    assert paused["enabled"] is False and paused["source"] == {"path": "/mnt/sources/finance"} and paused["files_synced"] == 1
    assert stack.http.patch(f"/api/v1/connectors/{created['id']}", json={"path": "/etc"}).status_code == 422

    assert stack.http.delete(f"/api/v1/connectors/{created['id']}").status_code == 204
    assert stack.http.get(f"/api/v1/connectors/{created['id']}").status_code == 404
    with stack.factory() as session:
        assert session.scalar(select(Connector)) is None and session.scalar(select(ConnectorFileState)) is None
        assert session.scalar(select(Document)).title == "leave.txt"  # stays as an ordinary Document


# --- S3-compatible storage ----------------------------------------------------------------------------------------- #
def s3_body(stack, **overrides):
    return {
        "name": "bucket-sync",
        "knowledge_base_id": stack.kb_id,
        "source_type": "s3",
        "endpoint_url": "http://minio.internal:9000/",
        "bucket": "docs",
        "prefix": "hr",
        "access_key_ref": "env:MINIO_ACCESS_KEY",
        "secret_key_ref": "env:MINIO_SECRET_KEY",
        **overrides,
    }


def test_an_s3_connector_stores_references_never_secrets(stack):
    response = stack.http.post("/api/v1/connectors", json=s3_body(stack))

    assert response.status_code == 201, response.text
    assert response.json()["source"] == {
        "endpoint_url": "http://minio.internal:9000",
        "bucket": "docs",
        "prefix": "hr/",
        "access_key_ref": "env:MINIO_ACCESS_KEY",
        "secret_key_ref": "env:MINIO_SECRET_KEY",
    }
    with stack.factory() as session:
        stored = session.scalar(select(Connector)).source_config
    assert all(v.startswith("env:") for k, v in stored.items() if k.endswith("_ref"))


@pytest.mark.parametrize("field", ["access_key_ref", "secret_key_ref"])
def test_a_literal_credential_is_refused_and_nothing_is_stored(stack, field):
    response = stack.http.post("/api/v1/connectors", json=s3_body(stack, **{field: "AKIAIOSFODNN7EXAMPLE"}))

    assert response.status_code == 422
    with stack.factory() as session:
        assert session.scalar(select(Connector)) is None


def test_an_s3_connector_needs_its_endpoint_bucket_and_credentials(stack):
    for missing in ("endpoint_url", "bucket", "access_key_ref", "secret_key_ref"):
        body_ = s3_body(stack)
        del body_[missing]
        assert stack.http.post("/api/v1/connectors", json=body_).status_code == 422, missing
    assert stack.http.post("/api/v1/connectors", json=s3_body(stack, path="/mnt/sources/x")).status_code == 422
    assert stack.http.post("/api/v1/connectors", json=body(stack, bucket="docs")).status_code == 422


def test_the_endpoint_must_be_http_and_on_the_hosts_allowlist(stack):
    assert stack.http.post("/api/v1/connectors", json=s3_body(stack, endpoint_url="minio.internal")).status_code == 422

    stack.use_settings(
        Settings(auth=AuthSettings(mode="jwt"), connector=ConnectorSettings(mount_root="/mnt/sources", allowed_s3_hosts="minio.internal"))
    )
    refused = stack.http.post("/api/v1/connectors", json=s3_body(stack, endpoint_url="http://evil.example"))
    assert refused.status_code == 422 and "CONNECTOR__ALLOWED_S3_HOSTS" in refused.text
    assert stack.http.post("/api/v1/connectors", json=s3_body(stack)).status_code == 201


def test_an_s3_connector_can_be_repointed_but_not_given_a_path(stack):
    created = stack.http.post("/api/v1/connectors", json=s3_body(stack)).json()
    url = f"/api/v1/connectors/{created['id']}"

    changed = stack.http.patch(url, json={"bucket": "archive", "prefix": "", "secret_key_ref": "env:OTHER_SECRET"}).json()

    assert changed["source"]["bucket"] == "archive" and changed["source"]["secret_key_ref"] == "env:OTHER_SECRET"
    assert changed["source"]["access_key_ref"] == "env:MINIO_ACCESS_KEY"
    assert stack.http.patch(url, json={"path": "/mnt/sources/x"}).status_code == 422
    assert stack.http.patch(url, json={"secret_key_ref": "plain"}).status_code == 422
