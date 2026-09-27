"""Resolve the caller of a request, according to ``AUTH__MODE``."""

import logging
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

import jwt
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from src.config import AuthSettings, Settings

from .principal import AuthMethod, Principal, as_values, claim_at, map_roles

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """The request carries no acceptable identity.

    The message is for server logs only; clients get a generic 401 so a probe
    cannot learn which check failed.
    """


class Authenticator(Protocol):
    mode: str

    async def authenticate(self, request: Request) -> Principal: ...


class DisabledAuthenticator:
    """Auth off: every request is the anonymous all-roles principal."""

    mode = "disabled"

    async def authenticate(self, request: Request) -> Principal:
        return Principal.anonymous()


def build_principal(
    subject: Any,
    raw_roles: frozenset[str],
    groups: frozenset[str],
    auth_method: AuthMethod,
    settings: AuthSettings,
) -> Principal:
    """Map an asserted identity onto a Principal.

    An identity that maps to no platform role is rejected rather than admitted
    with none: a valid token for an unrelated application of the same IdP must
    not reach any route.
    """
    if not subject or not str(subject).strip():
        raise AuthenticationError("identity has no subject")
    roles = map_roles(raw_roles, settings)
    if not roles:
        raise AuthenticationError(f"subject {subject!r} maps to no platform role (roles={sorted(raw_roles)})")
    return Principal(
        subject=str(subject).strip(),
        roles=roles,
        groups=groups,
        raw_roles=raw_roles,
        auth_method=auth_method,
    )


# Symmetric and unsigned algorithms are refused: with HS* anyone holding the
# shared secret -- including every relying party -- could mint admin tokens.
_FORBIDDEN_ALGORITHM_PREFIXES = ("HS", "NONE")


class JwtAuthenticator:
    """Validate a bearer JWT issued by the buyer's IdP on every request.

    Stateless: nothing is stored, so revoking a role at the IdP takes effect
    when the token expires. Keys come from the IdP's JWKS (cached, refetched on
    an unknown ``kid`` subject to PyJWT's cooldown) or, for air-gapped
    deployments, from a static PEM public key.
    """

    mode = "jwt"

    def __init__(self, settings: AuthSettings, jwks_client: Optional[jwt.PyJWKClient] = None):
        self.settings = settings
        self.algorithms = settings.algorithm_list

        problems = []
        if not settings.issuer:
            problems.append("AUTH__ISSUER is required")
        if not settings.audience:
            problems.append("AUTH__AUDIENCE is required")
        if not (settings.jwks_url or settings.public_key_path or jwks_client):
            problems.append("set AUTH__JWKS_URL or AUTH__PUBLIC_KEY_PATH")
        if not self.algorithms:
            problems.append("AUTH__ALGORITHMS is empty")
        bad = [a for a in self.algorithms if a.upper().startswith(_FORBIDDEN_ALGORITHM_PREFIXES)]
        if bad:
            problems.append(f"AUTH__ALGORITHMS must be asymmetric; refusing {bad}")
        if problems:
            raise ValueError("Invalid jwt auth configuration: " + "; ".join(problems))

        self._public_key: Optional[str] = None
        self._jwks: Optional[jwt.PyJWKClient] = jwks_client
        if self._jwks is None:
            if settings.public_key_path:
                self._public_key = Path(settings.public_key_path).read_text()
            else:
                self._jwks = jwt.PyJWKClient(
                    settings.jwks_url,
                    cache_keys=True,
                    lifespan=settings.jwks_cache_seconds,
                    timeout=10,
                )

    async def authenticate(self, request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("missing bearer token")
        claims = await self.decode(token)
        return self.principal_from_claims(claims)

    async def decode(self, token: str) -> Mapping[str, Any]:
        """Verify signature, issuer, audience and validity window."""
        try:
            key = self._public_key if self._public_key is not None else await run_in_threadpool(self._jwks_key, token)
            return jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.leeway_seconds,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"invalid token: {exc}") from None

    def _jwks_key(self, token: str) -> Any:
        assert self._jwks is not None
        return self._jwks.get_signing_key_from_jwt(token).key

    def principal_from_claims(self, claims: Mapping[str, Any]) -> Principal:
        s = self.settings
        return build_principal(
            subject=claim_at(claims, s.subject_claim),
            raw_roles=as_values(claim_at(claims, s.roles_claim)),
            groups=as_values(claim_at(claims, s.groups_claim)),
            auth_method="jwt",
            settings=s,
        )


def make_authenticator(settings: Settings) -> Authenticator:
    """Build the authenticator for the configured mode.

    :raises ValueError: if the configuration is incomplete for the mode, so a
        misconfigured deployment fails at startup rather than on first request.
    """
    mode = settings.auth.mode
    if mode == "disabled":
        if settings.environment == "production":
            logger.warning("AUTH__MODE=disabled in production: every API route is unauthenticated")
        return DisabledAuthenticator()
    if mode == "jwt":
        return JwtAuthenticator(settings.auth)
    raise ValueError(f"AUTH__MODE={mode!r} is not supported yet")
