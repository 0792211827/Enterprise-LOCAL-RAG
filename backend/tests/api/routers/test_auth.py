"""/api/v1/auth/me with auth disabled (the default)."""


async def test_me_returns_anonymous_principal_when_disabled(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["subject"] == "api_user"
    assert data["auth_method"] == "disabled"
    assert set(data["roles"]) == {"admin", "querier", "auditor"}


async def test_every_response_carries_request_id(client):
    response = await client.get("/api/v1/health")
    assert "x-request-id" in response.headers
