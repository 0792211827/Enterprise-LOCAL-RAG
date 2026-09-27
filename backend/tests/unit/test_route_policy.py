"""Every API route has a deliberate access policy.

Enumerates the real application's routes. A new route mounted without a role
guard -- or an admin route accidentally mounted with a weaker one -- fails here
instead of shipping open.
"""

import pytest
from fastapi.routing import APIRoute
from src.auth import Role
from src.main import app

# Routes authorised some other way, each for a stated reason.
EXEMPT = {
    ("GET", "/api/v1/health"): "public liveness probe for orchestrators",
    ("GET", "/api/v1/auth/me"): "any authenticated caller (get_principal) may inspect their own mapping",
    ("POST", "/v1/chat/completions"): "authorised in the handler: accepts app API keys as well as user tokens",
}

QUERY_PREFIXES = ("/api/v1/ask", "/api/v1/stream", "/api/v1/ask-agentic", "/api/v1/feedback", "/api/v1/hybrid-search")
ADMIN_PREFIXES = (
    "/api/v1/knowledge-bases",
    "/api/v1/documents",
    "/api/v1/ingestion-jobs",
    "/api/v1/applications",
    "/api/v1/providers",
    "/api/v1/retrieval-configurations",
    "/api/v1/system",
)
AUDITOR_PREFIXES = ("/api/v1/audit",)


def _routes():
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, route.path, route


def _required_roles(route: APIRoute) -> frozenset[Role] | None:
    for dependency in route.dependencies:
        roles = getattr(dependency.dependency, "required_roles", None)
        if roles is not None:
            return roles
    return None


ROUTES = list(_routes())


def test_routes_were_discovered():
    assert len(ROUTES) > 30


@pytest.mark.parametrize("method, path, route", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
def test_route_has_policy(method, path, route):
    if (method, path) in EXEMPT:
        assert _required_roles(route) is None, f"{method} {path} is exempt but also carries a role guard"
        return
    roles = _required_roles(route)
    assert roles is not None, f"{method} {path} has no access policy; add it to main.py (or EXEMPT, with a reason)"

    if path.startswith(ADMIN_PREFIXES):
        assert roles == {Role.ADMIN}, f"{method} {path} must be admin-only, got {roles}"
    elif path.startswith(QUERY_PREFIXES):
        assert roles == {Role.QUERIER}, f"{method} {path} must require querier, got {roles}"
    elif path.startswith(AUDITOR_PREFIXES):
        assert roles == {Role.AUDITOR}, f"{method} {path} must be auditor-only, got {roles}"
    else:
        pytest.fail(f"{method} {path} is not classified; extend this test's prefixes deliberately")


def test_provider_url_fetching_endpoints_are_admin_only():
    """/test and /models make the backend call caller-supplied URLs (SSRF)."""
    paths = {p for _, p, r in ROUTES if p.startswith("/api/v1/providers/") and _required_roles(r) == {Role.ADMIN}}
    assert "/api/v1/providers/{provider_id}/test" in paths
    assert "/api/v1/providers/{provider_id}/models" in paths
