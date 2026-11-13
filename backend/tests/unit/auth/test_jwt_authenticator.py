"""JWT bearer validation against a JWKS or a static PEM key.

Keys are generated per test run and the JWKS is served in-process, so these
tests make no network calls.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from src.auth import AuthenticationError, Role
from src.auth.authenticators import JwtAuthenticator, make_authenticator
from src.config import AuthSettings, Settings
from starlette.requests import Request

ISSUER = "https://idp.example.internal/realms/acme"
AUDIENCE = "rag-platform"


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def signing_key():
    return _rsa_key()


@pytest.fixture(scope="module")
def other_key():
    return _rsa_key()


def _jwk(private_key, kid: str) -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "use": "sig", "alg": "RS256"}


class FakeJWKClient(jwt.PyJWKClient):
    """PyJWKClient whose JWKS comes from memory; counts fetches."""

    def __init__(self, keys: list[dict], **kwargs):
        super().__init__("https://idp.example.internal/jwks", cache_keys=True, **kwargs)
        self.keys = keys
        self.fetches = 0

    def fetch_data(self):
        self.fetches += 1
        data = {"keys": list(self.keys)}
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


def _settings(**overrides) -> AuthSettings:
    base = dict(
        mode="jwt",
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://idp.example.internal/jwks",
        admin_roles="rag-admin",
        query_roles="rag-user",
        auditor_roles="rag-auditor",
    )
    return AuthSettings(**{**base, **overrides})


def _token(private_key, kid="k1", alg="RS256", **claims) -> str:
    now = int(time.time())
    payload = {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 300, "roles": ["rag-user"]}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, private_key, algorithm=alg, headers={"kid": kid})


def _request(authorization: str | None) -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.fixture
def jwks(signing_key):
    return FakeJWKClient([_jwk(signing_key, "k1")])


@pytest.fixture
def authenticator(jwks):
    return JwtAuthenticator(_settings(), jwks_client=jwks)


class TestValidTokens:
    async def test_query_user(self, authenticator, signing_key):
        principal = await authenticator.authenticate(_request(f"Bearer {_token(signing_key)}"))
        assert principal.subject == "alice"
        assert principal.roles == {Role.QUERIER}
        assert principal.auth_method == "jwt"

    async def test_groups_and_raw_roles_are_kept(self, authenticator, signing_key):
        token = _token(signing_key, roles=["rag-user", "finance"], groups=["analysts", "emea"])
        principal = await authenticator.authenticate(_request(f"Bearer {token}"))
        assert principal.groups == {"analysts", "emea"}
        assert principal.raw_roles == {"rag-user", "finance"}

    async def test_dotted_roles_claim(self, jwks, signing_key):
        auth = JwtAuthenticator(_settings(roles_claim="realm_access.roles"), jwks_client=jwks)
        token = _token(signing_key, roles=None, realm_access={"roles": ["rag-admin"]})
        principal = await auth.authenticate(_request(f"Bearer {token}"))
        assert Role.ADMIN in principal.roles

    async def test_comma_delimited_groups_string(self, authenticator, signing_key):
        token = _token(signing_key, groups="analysts,emea")
        principal = await authenticator.authenticate(_request(f"Bearer {token}"))
        assert principal.groups == {"analysts", "emea"}

    async def test_scheme_is_case_insensitive(self, authenticator, signing_key):
        principal = await authenticator.authenticate(_request(f"bearer {_token(signing_key)}"))
        assert principal.subject == "alice"

    async def test_keys_are_cached(self, authenticator, jwks, signing_key):
        for _ in range(3):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key)}"))
        assert jwks.fetches == 1

    async def test_expiry_leeway(self, authenticator, signing_key):
        token = _token(signing_key, exp=int(time.time()) - 10)  # within default 30s leeway
        principal = await authenticator.authenticate(_request(f"Bearer {token}"))
        assert principal.subject == "alice"


class TestRejectedTokens:
    @pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer   ", "Basic dXNlcjpwYXNz"])
    async def test_missing_or_wrong_scheme(self, authenticator, header):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(header))

    async def test_expired(self, authenticator, signing_key):
        token = _token(signing_key, exp=int(time.time()) - 3600)
        with pytest.raises(AuthenticationError, match="expired"):
            await authenticator.authenticate(_request(f"Bearer {token}"))

    async def test_wrong_issuer(self, authenticator, signing_key):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key, iss='https://evil')}"))

    async def test_wrong_audience(self, authenticator, signing_key):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key, aud='other-app')}"))

    async def test_missing_exp(self, authenticator, signing_key):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key, exp=None)}"))

    async def test_signed_by_another_key_with_known_kid(self, authenticator, other_key):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {_token(other_key, kid='k1')}"))

    async def test_unknown_kid_refetches_once_then_fails(self, signing_key, other_key):
        jwks = FakeJWKClient([_jwk(signing_key, "k1")], cooldown_duration=0)
        auth = JwtAuthenticator(_settings(), jwks_client=jwks)
        await auth.authenticate(_request(f"Bearer {_token(signing_key)}"))
        with pytest.raises(AuthenticationError):
            await auth.authenticate(_request(f"Bearer {_token(other_key, kid='k2')}"))
        assert jwks.fetches == 2

    async def test_key_rotation_is_picked_up(self, signing_key, other_key):
        jwks = FakeJWKClient([_jwk(signing_key, "k1")], cooldown_duration=0)
        auth = JwtAuthenticator(_settings(), jwks_client=jwks)
        await auth.authenticate(_request(f"Bearer {_token(signing_key)}"))
        jwks.keys.append(_jwk(other_key, "k2"))  # IdP rotates in a new key
        principal = await auth.authenticate(_request(f"Bearer {_token(other_key, kid='k2')}"))
        assert principal.subject == "alice"

    async def test_hs256_token_is_refused(self, authenticator):
        token = jwt.encode(
            {"sub": "mallory", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "roles": ["rag-admin"]},
            "shared-secret-with-at-least-32-bytes-of-length!!",
            algorithm="HS256",
            headers={"kid": "k1"},
        )
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {token}"))

    async def test_unsigned_token_is_refused(self, authenticator):
        token = jwt.encode(
            {"sub": "mallory", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "roles": ["rag-admin"]},
            None,
            algorithm="none",
            headers={"kid": "k1"},
        )
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request(f"Bearer {token}"))

    async def test_no_mappable_role(self, authenticator, signing_key):
        with pytest.raises(AuthenticationError, match="no platform role"):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key, roles=['marketing'])}"))

    async def test_missing_subject(self, authenticator, signing_key):
        with pytest.raises(AuthenticationError, match="no subject"):
            await authenticator.authenticate(_request(f"Bearer {_token(signing_key, sub=None)}"))

    async def test_api_key_is_not_a_jwt(self, authenticator):
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(_request("Bearer sk-abc123"))


class TestStaticPublicKey:
    async def test_pem_file(self, tmp_path, signing_key):
        pem = signing_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        path = tmp_path / "idp.pem"
        path.write_bytes(pem)
        auth = JwtAuthenticator(_settings(jwks_url="", public_key_path=str(path)))
        principal = await auth.authenticate(_request(f"Bearer {_token(signing_key)}"))
        assert principal.subject == "alice"


class TestConfigurationValidation:
    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"issuer": ""}, "AUTH__ISSUER"),
            ({"audience": ""}, "AUTH__AUDIENCE"),
            ({"jwks_url": "", "public_key_path": ""}, "AUTH__JWKS_URL"),
            ({"algorithms": ""}, "AUTH__ALGORITHMS is empty"),
            ({"algorithms": "RS256,HS256"}, "asymmetric"),
            ({"algorithms": "none"}, "asymmetric"),
        ],
    )
    def test_incomplete_config_fails_fast(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            JwtAuthenticator(_settings(**overrides))

    def test_make_authenticator_selects_jwt(self):
        settings = Settings(auth=_settings())
        assert make_authenticator(settings).mode == "jwt"

    def test_make_authenticator_default_is_disabled(self):
        assert make_authenticator(Settings()).mode == "disabled"
