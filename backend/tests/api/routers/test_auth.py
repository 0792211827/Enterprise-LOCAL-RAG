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


async def test_agentic_ask_passes_caller_to_service(client):
    from unittest.mock import AsyncMock, MagicMock

    import src.main as main
    from src import dependencies
    from src.auth import Principal, Role, get_principal

    service = MagicMock()
    service.ask = AsyncMock(return_value={"query": "q", "answer": "a"})
    main.app.dependency_overrides[dependencies.get_agentic_rag_service] = lambda: service
    main.app.dependency_overrides[get_principal] = lambda: Principal(
        subject="frank", roles=frozenset({Role.QUERIER}), auth_method="jwt"
    )
    try:
        response = await client.post("/api/v1/ask-agentic", json={"query": "q"})
    finally:
        main.app.dependency_overrides.pop(get_principal, None)
        main.app.dependency_overrides.pop(dependencies.get_agentic_rag_service, None)

    assert response.status_code == 200
    assert service.ask.call_args.kwargs["user_id"] == "frank"
