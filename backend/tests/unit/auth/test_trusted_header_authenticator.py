"""Identity asserted by an authenticating reverse proxy."""

import pytest
from starlette.requests import Request
from src.auth import AuthenticationError, Role
from src.auth.authenticators import TrustedHeaderAuthenticator, make_authenticator
from src.config import AuthSettings, Settings


def _settings(**overrides) -> AuthSettings:
    base = dict(
        mode="trusted_header",
        trusted_proxy_cidrs="10.0.0.0/8",
        admin_roles="rag-admin",
        query_roles="rag-user",
        auditor_roles="rag-auditor",
    )
    return AuthSettings(**{**base, **overrides})


def _request(peer: str | None, **headers: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.replace("_", "-").lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 50000) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def auth():
    return TrustedHeaderAuthenticator(_settings())


class TestTrustedPeer:
    async def test_maps_headers(self, auth):
        principal = await auth.authenticate(
            _request(
                "10.1.2.3",
                X_Forwarded_User="bob",
                X_Forwarded_Roles="rag-user, finance",
                X_Forwarded_Groups="analysts,emea",
            )
        )
        assert principal.subject == "bob"
        assert principal.roles == {Role.QUERIER}
        assert principal.groups == {"analysts", "emea"}
        assert principal.raw_roles == {"rag-user", "finance"}
        assert principal.auth_method == "trusted_header"

    async def test_custom_header_names_and_separator(self):
        auth = TrustedHeaderAuthenticator(
            _settings(
                trusted_header_subject="X-Remote-User",
                trusted_header_roles="X-Remote-Roles",
                trusted_header_separator=";",
            )
        )
        principal = await auth.authenticate(
            _request("10.0.0.9", X_Remote_User="carol", X_Remote_Roles="rag-admin;x")
        )
        assert Role.ADMIN in principal.roles

    async def test_missing_subject(self, auth):
        with pytest.raises(AuthenticationError, match="no subject"):
            await auth.authenticate(_request("10.1.2.3", X_Forwarded_Roles="rag-user"))

    async def test_no_mappable_role(self, auth):
        with pytest.raises(AuthenticationError, match="no platform role"):
            await auth.authenticate(_request("10.1.2.3", X_Forwarded_User="bob", X_Forwarded_Roles="sales"))


class TestUntrustedPeer:
    async def test_direct_client_cannot_assert_identity(self, auth):
        with pytest.raises(AuthenticationError, match="untrusted peer"):
            await auth.authenticate(
                _request("203.0.113.7", X_Forwarded_User="admin", X_Forwarded_Roles="rag-admin")
            )

    async def test_forged_forwarded_for_is_ignored(self, auth):
        # The peer is what counts, not a client-supplied X-Forwarded-For.
        with pytest.raises(AuthenticationError, match="untrusted peer"):
            await auth.authenticate(
                _request(
                    "203.0.113.7",
                    X_Forwarded_For="10.0.0.1",
                    X_Forwarded_User="admin",
                    X_Forwarded_Roles="rag-admin",
                )
            )

    async def test_no_client_address(self, auth):
        with pytest.raises(AuthenticationError):
            await auth.authenticate(_request(None, X_Forwarded_User="bob", X_Forwarded_Roles="rag-user"))


class TestConfiguration:
    def test_proxy_cidrs_required(self):
        with pytest.raises(ValueError, match="AUTH__TRUSTED_PROXY_CIDRS is required"):
            TrustedHeaderAuthenticator(_settings(trusted_proxy_cidrs=""))

    def test_invalid_cidr(self):
        with pytest.raises(ValueError, match="Invalid AUTH__TRUSTED_PROXY_CIDRS"):
            TrustedHeaderAuthenticator(_settings(trusted_proxy_cidrs="10.0.0.0/8,proxy.local"))

    def test_make_authenticator_selects_trusted_header(self):
        assert make_authenticator(Settings(auth=_settings())).mode == "trusted_header"
