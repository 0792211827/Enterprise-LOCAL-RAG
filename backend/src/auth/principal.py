"""The authenticated caller of a request."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Literal, Mapping, Optional
from uuid import UUID

from src.config import AuthSettings

AuthMethod = Literal["disabled", "jwt", "trusted_header", "api_key"]

# Subject used when auth is disabled. Matches the value traces carried before
# identity intake existed, so observability data stays continuous.
ANONYMOUS_SUBJECT = "api_user"


class Role(str, Enum):
    """Platform roles, mapped from IdP claim values by configuration."""

    ADMIN = "admin"  # control plane: knowledge bases, documents, apps, keys
    QUERIER = "querier"  # answer and search endpoints
    AUDITOR = "auditor"  # read-only audit log


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    auth_method: AuthMethod
    groups: frozenset[str] = field(default_factory=frozenset)
    # Unmapped IdP role values: knowledge-base grants can target these directly.
    raw_roles: frozenset[str] = field(default_factory=frozenset)
    api_key_id: Optional[UUID] = None
    application_id: Optional[UUID] = None

    @classmethod
    def anonymous(cls) -> "Principal":
        """The caller when auth is disabled: holds every role, as before."""
        return cls(subject=ANONYMOUS_SUBJECT, roles=frozenset(Role), auth_method="disabled")

    def has_any_role(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)


def claim_at(claims: Mapping[str, Any], path: str) -> Any:
    """Read a claim by dotted path (``realm_access.roles``); None if absent."""
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def as_values(raw: Any, separator: str = ",") -> frozenset[str]:
    """Normalise a claim that may be a list or a delimited string."""
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        items: Iterable[Any] = raw.split(separator)
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = raw
    else:
        items = [raw]
    return frozenset(str(item).strip() for item in items if str(item).strip())


def map_roles(raw_roles: frozenset[str], settings: AuthSettings) -> frozenset[Role]:
    """Translate IdP role values into platform roles.

    ADMIN implies QUERIER, since operators test assistants from the admin UI.
    ADMIN implies AUDITOR only when ``admin_can_read_audit`` is on, so strict
    separation-of-duties deployments can keep the audit log out of admins' reach.
    """
    roles: set[Role] = set()
    if raw_roles & set(settings.admin_role_values):
        roles.add(Role.ADMIN)
        roles.add(Role.QUERIER)
        if settings.admin_can_read_audit:
            roles.add(Role.AUDITOR)
    if raw_roles & set(settings.query_role_values):
        roles.add(Role.QUERIER)
    if raw_roles & set(settings.auditor_role_values):
        roles.add(Role.AUDITOR)
    return frozenset(roles)
