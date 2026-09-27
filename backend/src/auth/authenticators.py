"""Resolve the caller of a request, according to ``AUTH__MODE``."""

import logging
from typing import Protocol

from fastapi import Request
from src.config import Settings

from .principal import Principal

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
    raise ValueError(f"AUTH__MODE={mode!r} is not supported yet")
