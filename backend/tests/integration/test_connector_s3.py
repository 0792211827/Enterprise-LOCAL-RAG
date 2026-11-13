"""The Connector against a live S3-compatible service (MinIO) and a live OpenSearch: the same add, change and delete
behaviour as a mounted folder, through the real boto3 client.

    docker run -d -p 9000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data

Needs both services: CONNECTOR_TEST_S3_ENDPOINT (default http://localhost:9000, credentials minioadmin/minioadmin
unless CONNECTOR_TEST_S3_ACCESS_KEY / _SECRET_KEY say otherwise) and OPENSEARCH__HOST (default http://localhost:9200);
skipped when either does not answer. Embeddings are faked: this tests the Connector, not the model.
"""

import asyncio
import os
import uuid

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from opensearchpy import OpenSearch
from sqlalchemy import select
from src.config import ConnectorSettings, IngestionSettings, Settings
from src.models import Connector, KnowledgeBase
from src.models.document import Document
from src.services.connectors import ConnectorSync
from src.services.ingestion.factory import make_ingestion_service
from src.services.opensearch.client import OpenSearchClient
from src.services.opensearch.index_config_hybrid import build_chunks_mapping

from tests.audit_support import opener, sqlite_session_factory
from tests.opensearch_fakes import FakeEmbeddings

S3_ENDPOINT = os.environ.get("CONNECTOR_TEST_S3_ENDPOINT", "http://localhost:9000")
OPENSEARCH_HOST = os.environ.get("OPENSEARCH__HOST", "http://localhost:9200")
DIMENSION = 2


@pytest.fixture
def bucket(monkeypatch):
    import boto3
    from botocore.config import Config

    access = os.environ.get("CONNECTOR_TEST_S3_ACCESS_KEY", "minioadmin")
    secret = os.environ.get("CONNECTOR_TEST_S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("CONNECTOR_TEST_ACCESS", access)
    monkeypatch.setenv("CONNECTOR_TEST_SECRET", secret)
    client = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=access, aws_secret_access_key=secret, region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, connect_timeout=3, retries={"max_attempts": 1}),
    )
    try:
        client.list_buckets()
    except Exception:
        pytest.skip(f"no S3-compatible service at {S3_ENDPOINT}")
    name = f"connector-{uuid.uuid4().hex[:10]}"
    client.create_bucket(Bucket=name)
    yield name, client
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
        for item in page.get("Contents", []):
            client.delete_object(Bucket=name, Key=item["Key"])
    client.delete_bucket(Bucket=name)


@pytest.fixture
def index(monkeypatch):
    cluster = OpenSearch(hosts=[OPENSEARCH_HOST], use_ssl=False, verify_certs=False, ssl_show_warn=False, timeout=30)
    try:
        cluster.info()
    except Exception:
        pytest.skip(f"no OpenSearch at {OPENSEARCH_HOST}")
    name = f"connector-{uuid.uuid4().hex[:10]}"
    cluster.indices.create(index=name, body=build_chunks_mapping(DIMENSION))
    yield cluster, name
    cluster.indices.delete(index=name, ignore_unavailable=True)


def test_a_bucket_is_mirrored_into_a_knowledge_base_with_add_change_delete_and_failure_handling(bucket, index, tmp_path):
    bucket_name, s3 = bucket
    cluster, index_name = index
    settings = Settings(
        connector=ConnectorSettings(allowed_s3_hosts="localhost,127.0.0.1"), ingestion=IngestionSettings(upload_dir=str(tmp_path))
    )
    factory = sqlite_session_factory()
    with factory() as session:
        kb = KnowledgeBase(name="hr", slug="hr", index_name="hr-idx")
        session.add(kb)
        session.commit()
        session.add(
            Connector(
                name="bucket",
                knowledge_base_id=kb.id,
                source_type="s3",
                source_config={
                    "endpoint_url": S3_ENDPOINT,
                    "bucket": bucket_name,
                    "prefix": "hr/",
                    "access_key_ref": "env:CONNECTOR_TEST_ACCESS",
                    "secret_key_ref": "env:CONNECTOR_TEST_SECRET",
                },
            )
        )
        session.commit()

    opensearch = OpenSearchClient.__new__(OpenSearchClient)
    opensearch.client, opensearch.index_name, opensearch.settings = cluster, index_name, settings
    embeddings = FakeEmbeddings(DIMENSION)
    sync = ConnectorSync(opener(factory), make_ingestion_service(opensearch, embeddings, None, settings), opensearch, settings)

    def put(key, text):
        s3.put_object(Bucket=bucket_name, Key=key, Body=text.encode())

    def run(**kwargs):
        return asyncio.run(sync.run("bucket", **kwargs))

    def titles():
        with factory() as session:
            return {d.title for d in session.scalars(select(Document))}

    def indexed():
        cluster.indices.refresh(index=index_name)
        hits = cluster.search(index=index_name, body={"query": {"match_all": {}}, "size": 100})["hits"]["hits"]
        return sorted(h["_source"]["chunk_text"] for h in hits)

    put("hr/leave.txt", "Employees get 25 days of annual leave.")
    put("hr/policies/remote.md", "# Remote\n\nRemote work is allowed two days a week.")
    put("finance/salaries.txt", "Outside the prefix, so never mirrored.")
    put("hr/slides.pptx", "unsupported type")

    first = run()
    assert (first.added, first.updated, first.removed, first.failed, first.skipped) == (2, 0, 0, 0, 1)
    assert titles() == {"leave.txt", "policies/remote.md"}
    assert any("25 days" in t for t in indexed()) and not any("Outside the prefix" in t for t in indexed())

    second = run()
    assert (second.added, second.updated, second.removed, second.unchanged) == (0, 0, 0, 2)

    put("hr/leave.txt", "Employees now get 30 days of annual leave.")
    third = run()
    assert (third.added, third.updated, third.removed) == (0, 1, 0)
    assert any("30 days" in t for t in indexed()) and not any("25 days" in t for t in indexed())

    s3.delete_object(Bucket=bucket_name, Key="hr/policies/remote.md")
    fourth = run()
    assert (fourth.added, fourth.updated, fourth.removed) == (0, 0, 1)
    assert titles() == {"leave.txt"} and not any("Remote work" in t for t in indexed())
