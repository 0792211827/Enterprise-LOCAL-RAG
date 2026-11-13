"""Role enforcement on the real application, with the caller overridden."""

import pytest
import src.main as main
from src.auth import Principal, Role, get_principal


@pytest.fixture
def as_caller():
    def _set(*roles: Role):
        main.app.dependency_overrides[get_principal] = lambda: Principal(
            subject="tester", roles=frozenset(roles), auth_method="jwt"
        )

    yield _set
    main.app.dependency_overrides.pop(get_principal, None)


ADMIN_GETS = ["/api/v1/knowledge-bases", "/api/v1/providers", "/api/v1/system/stats", "/api/v1/ingestion-jobs"]


@pytest.mark.parametrize("path", ADMIN_GETS)
async def test_querier_cannot_reach_admin_routes(client, as_caller, path):
    as_caller(Role.QUERIER)
    response = await client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient role"}


async def test_querier_cannot_mint_api_keys(client, as_caller):
    as_caller(Role.QUERIER)
    response = await client.post(
        "/api/v1/applications/00000000-0000-0000-0000-000000000001/api-keys", json={"name": "x"}
    )
    assert response.status_code == 403


async def test_querier_cannot_probe_provider_urls(client, as_caller):
    as_caller(Role.QUERIER)
    response = await client.post("/api/v1/providers/00000000-0000-0000-0000-000000000001/test")
    assert response.status_code == 403


async def test_auditor_cannot_query(client, as_caller):
    as_caller(Role.AUDITOR)
    response = await client.post("/api/v1/ask", json={"query": "anything"})
    assert response.status_code == 403


async def test_auditor_cannot_administer(client, as_caller):
    as_caller(Role.AUDITOR)
    assert (await client.get("/api/v1/knowledge-bases")).status_code == 403


async def test_querier_can_query(client, as_caller):
    as_caller(Role.QUERIER)
    response = await client.post("/api/v1/ask", json={"query": "What are neural networks?"})
    assert response.status_code == 200


async def test_admin_passes_the_admin_guard(client, as_caller):
    as_caller(Role.ADMIN, Role.QUERIER)
    # The route's own logic runs (a 403 would mean the guard rejected the caller).
    response = await client.get("/api/v1/system/gpu")
    assert response.status_code == 200


async def test_public_health_needs_no_role(client, as_caller):
    as_caller()  # authenticated but holding no role at all
    assert (await client.get("/api/v1/health")).status_code == 200


async def test_disabled_mode_keeps_every_route_open(client):
    assert (await client.get("/api/v1/system/gpu")).status_code == 200
    assert (await client.post("/api/v1/ask", json={"query": "q"})).status_code == 200


async def test_security_status_reports_defaults(client):
    data = (await client.get("/api/v1/system/security")).json()
    assert data == {"auth_mode": "disabled", "api_keys_enforced": False, "user_token_header": None}


async def test_security_status_is_admin_only(client, as_caller):
    as_caller(Role.QUERIER)
    assert (await client.get("/api/v1/system/security")).status_code == 403


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/api/v1/providers/00000000-0000-0000-0000-000000000001/models", None),
        ("POST", "/api/v1/applications", {"name": "x", "llm_endpoint": "http://anything/v1"}),
        ("PATCH", "/api/v1/applications/00000000-0000-0000-0000-000000000001", {"llm_endpoint": "http://anything/v1"}),
    ],
)
@pytest.mark.parametrize("role", [Role.QUERIER, Role.AUDITOR])
async def test_only_admins_can_set_or_probe_endpoints(client, as_caller, role, method, path, body):
    as_caller(role)
    assert (await client.request(method, path, json=body)).status_code == 403


@pytest.mark.parametrize("role", [Role.QUERIER, Role.AUDITOR])
async def test_only_admins_can_upload_documents(client, as_caller, role):
    as_caller(role)
    response = await client.post(
        "/api/v1/knowledge-bases/00000000-0000-0000-0000-000000000001/documents",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403
