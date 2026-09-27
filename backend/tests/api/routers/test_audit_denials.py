"""401/403s on the real application are audited, with the caller when known."""

import pytest
import src.main as main
from src.auth import AuthenticationError, Principal, Role
from src.services.audit import DatabaseAuditRecorder
from tests.audit_support import events, opener, sqlite_session_factory


class _FixedAuthenticator:
    """Authenticates every request as one caller, through the real get_principal
    (which is what stores the principal on request.state for the middleware)."""

    mode = "jwt"

    def __init__(self, principal: Principal):
        self.principal = principal

    async def authenticate(self, request):
        return self.principal


@pytest.fixture
def recorded(client, monkeypatch):
    factory = sqlite_session_factory()
    monkeypatch.setattr(main.app.state, "audit_recorder", DatabaseAuditRecorder(opener(factory)))
    return factory


@pytest.fixture
def as_caller(client, monkeypatch):
    def _set(*roles: Role):
        principal = Principal(subject="mallory", roles=frozenset(roles), auth_method="jwt")
        monkeypatch.setattr(main.app.state, "authenticator", _FixedAuthenticator(principal))

    return _set


async def test_forbidden_admin_change(client, recorded, as_caller):
    as_caller(Role.QUERIER)
    response = await client.post("/api/v1/knowledge-bases", json={"name": "x"}, headers={"X-Request-ID": "rid-1"})
    assert response.status_code == 403

    [event] = events(recorded)
    assert (event.action, event.outcome, event.status_code) == ("admin.mutation", "denied", 403)
    assert (event.actor_subject, event.actor_roles, event.request_id) == ("mallory", ["querier"], "rid-1")


async def test_forbidden_read(client, recorded, as_caller):
    as_caller(Role.QUERIER)
    assert (await client.get("/api/v1/providers")).status_code == 403
    [event] = events(recorded)
    assert (event.action, event.route, event.http_method) == ("access.denied", "/api/v1/providers", "GET")


async def test_forbidden_query_is_recorded_by_the_middleware(client, recorded, as_caller):
    as_caller(Role.AUDITOR)
    assert (await client.post("/api/v1/ask", json={"query": "q"})).status_code == 403
    [event] = events(recorded)
    assert (event.action, event.actor_subject) == ("access.denied", "mallory")
    assert event.query_sha256 is None  # the handler never ran, so the query is not known


async def test_allowed_reads_are_not_recorded(client, recorded):
    assert (await client.get("/api/v1/health")).status_code == 200
    assert events(recorded) == []


async def test_off_records_nothing(client, as_caller):
    as_caller(Role.QUERIER)
    assert (await client.post("/api/v1/knowledge-bases", json={"name": "x"})).status_code == 403
    assert main.app.state.audit_recorder.enabled is False


async def test_unauthenticated_request(client, recorded, monkeypatch):
    class _Rejecting(_FixedAuthenticator):
        async def authenticate(self, request):
            raise AuthenticationError("no token")

    monkeypatch.setattr(main.app.state, "authenticator", _Rejecting(None))
    assert (await client.get("/api/v1/knowledge-bases")).status_code == 401
    [event] = events(recorded)
    assert (event.action, event.status_code, event.actor_subject) == ("access.denied", 401, None)
