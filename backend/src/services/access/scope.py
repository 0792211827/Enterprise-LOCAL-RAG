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
from src.models.access import EVERYONE_VALUE, GrantPrincipalType, principal_key
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
    :param principal_keys: the caller's Principals (``group:finance``...), which a Document restriction is matched
        against; ``None`` applies no restriction filter (an unrestricted caller).
    """

    permitted: Optional[List[str]]
    dropped: List[str] = field(default_factory=list)
    requested: Optional[List[str]] = None
    principal_keys: Optional[frozenset[str]] = None


@dataclass(frozen=True)
class RetrievalScope:
    """Knowledge bases a caller may read, and the Principals they hold. ``kb_ids=None`` means unrestricted.

    ``principal_keys`` narrows *within* those Knowledge Bases: a Document with restrictions is readable only if one of
    them is among the keys (see ``permits_document``). ``None`` means no Document is hidden.
    """

    kb_ids: Optional[frozenset[str]]
    reason: str
    principal_keys: Optional[frozenset[str]] = None

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
            return ScopeDecision(permitted=permitted, principal_keys=self.principal_keys)

        wanted = list(dict.fromkeys(str(kb_id) for kb_id in requested))
        if self.kb_ids is None:
            return ScopeDecision(permitted=wanted, requested=wanted, principal_keys=self.principal_keys)
        return ScopeDecision(
            permitted=[kb_id for kb_id in wanted if kb_id in self.kb_ids],
            dropped=[kb_id for kb_id in wanted if kb_id not in self.kb_ids],
            requested=wanted,
            principal_keys=self.principal_keys,
        )

    def permits_document(self, kb_id: str, restriction_keys: Iterable[str]) -> bool:
        """Whether the caller may read a Document: a Grant on its Knowledge Base AND (no restrictions OR any matches)."""
        if not self.restrict([str(kb_id)]).permitted:
            return False
        keys = set(restriction_keys)
        return self.principal_keys is None or not keys or bool(keys & self.principal_keys)

    def cache_token(self) -> Optional[str]:
        """A stable digest of the scope for cache keys; None when unrestricted.

        Unrestricted callers keep today's cache keys unchanged; restricted
        callers only ever share cached answers with callers of identical scope
        -- the same Knowledge Bases *and* the same Principals, since a Document
        restriction can show two callers of one Knowledge Base different Documents.
        """
        if self.kb_ids is None:
            return None
        material = ",".join(sorted(self.kb_ids))
        if self.principal_keys is not None:
            material += "|" + ",".join(sorted(self.principal_keys))
        return hashlib.sha256(material.encode()).hexdigest()[:16]


def caller_principal_keys(subject: str, groups: Iterable[str], roles: Iterable[str]) -> frozenset[str]:
    """The Principals a caller holds, in the vocabulary Grants and Document restrictions use."""
    keys = {principal_key(GrantPrincipalType.EVERYONE.value, EVERYONE_VALUE), principal_key(GrantPrincipalType.SUBJECT.value, subject)}
    keys.update(principal_key(GrantPrincipalType.GROUP.value, group) for group in groups)
    keys.update(principal_key(GrantPrincipalType.ROLE.value, role) for role in roles)
    return frozenset(keys)


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

    return RetrievalScope(
        kb_ids=frozenset(readable),
        reason="grants",
        principal_keys=caller_principal_keys(principal.subject, principal.groups, principal.raw_roles),
    )
