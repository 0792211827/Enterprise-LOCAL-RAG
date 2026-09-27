"""Which knowledge bases a caller may retrieve from.

This is the single place retrieval permissions are decided. Every search path
asks for a scope here and intersects it with what it was going to search,
rather than each path interpreting grants (and the admin bypass) on its own.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from sqlalchemy import select
from src.auth import Principal, Role
from src.config import Settings
from src.models import KnowledgeBase
from src.repositories import GrantRepository

NO_ACCESSIBLE_KNOWLEDGE_MESSAGE = (
    "You don't have access to any knowledge bases, so there is nothing to answer "
    "from. Ask an administrator for access."
)


@dataclass(frozen=True)
class ScopeDecision:
    """The outcome of applying a scope to one request.

    :param permitted: knowledge-base IDs to search; ``None`` means unscoped
        (search everything), ``[]`` means nothing.
    :param dropped: requested IDs the caller may not read (for the audit log).
    :param requested: what the path would have searched without permissions.
    """

    permitted: Optional[List[str]]
    dropped: List[str] = field(default_factory=list)
    requested: Optional[List[str]] = None


@dataclass(frozen=True)
class RetrievalScope:
    """Knowledge bases a caller may read. ``kb_ids=None`` means unrestricted."""

    kb_ids: Optional[frozenset[str]]
    reason: str

    @classmethod
    def unrestricted(cls, reason: str) -> "RetrievalScope":
        return cls(kb_ids=None, reason=reason)

    @property
    def is_unrestricted(self) -> bool:
        return self.kb_ids is None

    def restrict(self, requested: Optional[Iterable[str]] = None) -> ScopeDecision:
        """Intersect what a path wants to search with what the caller may read.

        ``requested=None`` is a global search (legacy endpoints): it becomes
        exactly the permitted set -- which may be empty, and an empty result
        must then search nothing, never everything.
        """
        if requested is None:
            permitted = None if self.kb_ids is None else sorted(self.kb_ids)
            return ScopeDecision(permitted=permitted)

        wanted = list(dict.fromkeys(str(kb_id) for kb_id in requested))
        if self.kb_ids is None:
            return ScopeDecision(permitted=wanted, requested=wanted)
        return ScopeDecision(
            permitted=[kb_id for kb_id in wanted if kb_id in self.kb_ids],
            dropped=[kb_id for kb_id in wanted if kb_id not in self.kb_ids],
            requested=wanted,
        )

    def cache_token(self) -> Optional[str]:
        """A stable digest of the scope for cache keys; None when unrestricted.

        Unrestricted callers keep today's cache keys unchanged; restricted
        callers only ever share cached answers with callers of identical scope.
        """
        if self.kb_ids is None:
            return None
        return hashlib.sha256(",".join(sorted(self.kb_ids)).encode()).hexdigest()[:16]


def resolve_scope(principal: Principal, session, settings: Settings) -> RetrievalScope:
    """Decide which knowledge bases ``principal`` may retrieve from."""
    auth = settings.auth
    if auth.mode == "disabled":
        return RetrievalScope.unrestricted("authentication disabled")

    # An application key with no forwarded end user is the application acting
    # on its own: the admin bound the key to that application, so its attached
    # knowledge bases are the limit (enforced by the application path itself).
    if principal.auth_method == "api_key":
        return RetrievalScope.unrestricted("application API key")

    if auth.admin_bypass_retrieval and Role.ADMIN in principal.roles:
        return RetrievalScope.unrestricted("admin bypass")

    repo = GrantRepository(session)
    readable = repo.readable_knowledge_base_ids(principal.subject, principal.groups, principal.raw_roles)

    if auth.ungranted_kb_access == "allow":
        every_kb = {str(kb_id) for kb_id in session.scalars(select(KnowledgeBase.id))}
        readable |= every_kb - repo.granted_knowledge_base_ids()

    return RetrievalScope(kb_ids=frozenset(readable), reason="grants")
