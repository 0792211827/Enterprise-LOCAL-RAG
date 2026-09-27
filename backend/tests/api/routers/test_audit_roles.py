"""On the real application, the audit API is auditor-only."""

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


@pytest.mark.parametrize(
    "roles, status",
    [
        ((Role.QUERIER,), 403),
        ((Role.AUDITOR,), 200),
        # What an admin holds when AUTH__ADMIN_CAN_READ_AUDIT=false.
        ((Role.ADMIN, Role.QUERIER), 403),
        ((Role.ADMIN, Role.QUERIER, Role.AUDITOR), 200),
    ],
)
async def test_audit_api_roles(client, as_caller, roles, status):
    as_caller(*roles)
    assert (await client.get("/api/v1/audit/status")).status_code == status
    assert (await client.get("/api/v1/audit/events")).status_code == status


async def test_disabled_mode_leaves_it_open_like_every_route(client):
    assert (await client.get("/api/v1/audit/status")).json()["level"] == "off"
