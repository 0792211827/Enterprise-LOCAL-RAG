"""A stale index dimension refuses ingestion while the rest of the API stays up."""

import pytest


@pytest.fixture
def index_dimension():
    return 512


async def test_ingestion_is_refused_with_409(client):
    response = await client.post(
        "/api/v1/knowledge-bases/00000000-0000-0000-0000-000000000001/documents",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 409
    assert "512" in response.json()["detail"] and "1024" in response.json()["detail"]
    assert "reindex" in response.json()["detail"].lower()


async def test_reprocess_is_refused_with_409(client):
    response = await client.post("/api/v1/documents/00000000-0000-0000-0000-000000000001/reprocess")
    assert response.status_code == 409


async def test_search_and_health_stay_up(client):
    assert (await client.get("/api/v1/health")).status_code == 200
    search = await client.post("/api/v1/hybrid-search/", json={"query": "neural networks"})
    assert search.status_code == 200, search.text
