"""Authorise a call to the OpenAI-compatible ``/v1/chat/completions``.

This endpoint has two kinds of caller, which is why it is authorised here
rather than by a router-level role guard:

* a **host application**, presenting its per-application API key
  (``Authorization: Bearer sk-...``), optionally forwarding its end user's
  identity in ``AUTH__USER_TOKEN_HEADER`` so retrieval can later be scoped to
  that user;
* an **end user** directly, with their own IdP token (or trusted-proxy headers).

Behaviour by configuration:

====================  ====================================================
``API_KEYS__ENFORCE`` Effect
====================  ====================================================
false (default)       As before: a key valid for the requested application
                      is recorded (``last_used_at``); requests without one
                      are still served -- anonymously with auth disabled,
                      or as the authenticated user otherwise.
true                  A key that exists, is not revoked and was issued for
                      the requested application is required.
====================  ====================================================
"""

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import Request
from src.config import Settings
from src.repositories import ApiKeyRepository

from .authenticators import AuthenticationError, Authenticator, JwtAuthenticator
from .dependencies import get_authenticator
from .principal import Principal, Role


@dataclass
class CompletionAuthError(Exception):
    """Rejection rendered as an OpenAI-shaped error by the router."""

    status_code: int
    code: str
    message: str
    err_type: str = "invalid_request_error"


def _bearer_api_key(authorization: Optional[str]) -> Optional[str]:
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token.startswith("sk-"):
        return token
    return None


def _require_querier(principal: Principal) -> Principal:
    if not principal.has_any_role(Role.QUERIER):
        raise CompletionAuthError(403, "insufficient_permissions", "Your role does not permit chat completions.")
    return principal


async def _end_user(request: Request, authenticator: Authenticator) -> Principal:
    """Authenticate the caller as an end user, or raise a 401."""
    try:
        return _require_querier(await authenticator.authenticate(request))
    except AuthenticationError:
        raise CompletionAuthError(401, "invalid_api_key", "Missing or invalid credentials.", "authentication_error")


async def _on_behalf_of(request: Request, settings: Settings, authenticator: Authenticator) -> Optional[Principal]:
    """The end user a host application forwarded, if any.

    Absent means "no end user"; present but invalid is a hard failure -- a host
    app that intends to scope a request to a user must not silently get the
    application's wider access instead.
    """
    if isinstance(authenticator, JwtAuthenticator):
        header = settings.auth.user_token_header
        raw = request.headers.get(header) if header else None
        if not raw:
            return None
        token = raw.split(" ", 1)[1].strip() if raw.lower().startswith("bearer ") else raw.strip()
        try:
            return _require_querier(await authenticator.authenticate_token(token))
        except AuthenticationError:
            raise CompletionAuthError(401, "invalid_user_token", f"Invalid end-user token in {header}.", "authentication_error")

    if authenticator.mode == "trusted_header":
        if not request.headers.get(settings.auth.trusted_header_subject):
            return None
        try:
            return _require_querier(await authenticator.authenticate(request))
        except AuthenticationError:
            raise CompletionAuthError(401, "invalid_user_identity", "Invalid forwarded user identity.", "authentication_error")

    return None


async def authorize_completion(
    request: Request,
    session: Any,
    settings: Settings,
    model: str,
    find_application: Callable[[], Any],
) -> tuple[Principal, Any]:
    """Resolve the caller and the target application.

    Callers are authenticated *before* the application is looked up whenever
    credentials are required, so an unauthenticated probe cannot tell existing
    application slugs from missing ones.

    :returns: ``(principal, application)``
    :raises CompletionAuthError: 401/403 for credentials, 404 for an unknown model
    """
    authenticator = get_authenticator(request)
    auth_enabled = authenticator.mode != "disabled"
    enforce = settings.api_keys.enforce
    repo = ApiKeyRepository(session)

    key = None
    raw_key = _bearer_api_key(request.headers.get("authorization"))
    if raw_key:
        key = repo.get_by_raw_key(raw_key)
        if key is not None and key.revoked_at is not None:
            key = None

    if enforce and key is None:
        raise CompletionAuthError(401, "invalid_api_key", "Missing or invalid API key.", "authentication_error")

    principal: Optional[Principal] = None
    if key is None and auth_enabled:
        principal = await _end_user(request, authenticator)

    application = find_application()
    if application is None:
        raise CompletionAuthError(
            404,
            "model_not_found",
            f"Model '{model}' not found. Use a RAG application slug as the model name.",
        )

    if key is not None and key.application_id != application.id:
        if enforce:
            raise CompletionAuthError(401, "invalid_api_key", "API key is not valid for this model.", "authentication_error")
        key = None  # not enforcing: ignore it, and don't record usage against it
        if auth_enabled and principal is None:
            principal = await _end_user(request, authenticator)

    if key is not None:
        repo.touch(key)
        user = await _on_behalf_of(request, settings, authenticator)
        if user is not None:
            principal = dataclasses.replace(user, api_key_id=key.id, application_id=application.id)
        else:
            principal = Principal(
                subject=f"api-key:{key.id}",
                roles=frozenset({Role.QUERIER}),
                auth_method="api_key",
                api_key_id=key.id,
                application_id=application.id,
            )
    elif principal is None:
        principal = Principal.anonymous()

    request.state.principal = principal
    return principal, application
