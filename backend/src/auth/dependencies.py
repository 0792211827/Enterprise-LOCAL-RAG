"""FastAPI dependencies for identity."""

import logging

from fastapi import HTTPException, Request, status

from .authenticators import AuthenticationError, Authenticator, make_authenticator
from .principal import Principal

logger = logging.getLogger(__name__)


def _authenticator(request: Request) -> Authenticator:
    """Return the app's authenticator, building it from settings if absent.

    The lifespan normally installs it. Apps assembled without the lifespan (e.g.
    router-level test apps) get one built from the same configuration -- never an
    implicit "allow everyone" fallback, which would silently open a secured
    deployment.
    """
    authenticator = getattr(request.app.state, "authenticator", None)
    if authenticator is None:
        from src.dependencies import get_settings

        authenticator = make_authenticator(get_settings())
        request.app.state.authenticator = authenticator
    return authenticator


async def get_principal(request: Request) -> Principal:
    """Resolve the caller, or reject the request with 401."""
    try:
        principal = await _authenticator(request).authenticate(request)
    except AuthenticationError as exc:
        logger.info("Authentication failed for %s %s: %s", request.method, request.url.path, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    # Read by the audit middleware, which runs outside dependency injection.
    request.state.principal = principal
    return principal
