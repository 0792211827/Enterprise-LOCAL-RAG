"""FastAPI dependencies for identity."""

import logging
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from .authenticators import AuthenticationError, Authenticator, make_authenticator
from .principal import Principal, Role

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


def require_role(*roles: Role) -> Callable:
    """Dependency factory: admit callers holding any of ``roles``, else 403.

    With auth disabled the anonymous principal holds every role, so this is a
    no-op until an operator turns authentication on.
    """
    if not roles:
        raise ValueError("require_role needs at least one role")

    async def check_role(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any_role(*roles):
            logger.info(
                "Forbidden: %s (roles=%s) on %s %s needs one of %s",
                principal.subject,
                sorted(r.value for r in principal.roles),
                request.method,
                request.url.path,
                [r.value for r in roles],
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return principal

    # Lets the route-policy test assert which roles guard each route.
    check_role.required_roles = frozenset(roles)  # type: ignore[attr-defined]
    return check_role
