"""Who may retrieve from a knowledge base."""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import backref, relationship
from src.db.interfaces.postgresql import Base
from src.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GrantPrincipalType(str, Enum):
    """What a grant's ``principal_value`` is matched against.

    Values are IdP claim values, not local user IDs: the platform does not own
    users, it trusts the buyer's IdP to say who is in which group.
    """

    GROUP = "group"  # a value of the caller's groups claim
    ROLE = "role"  # a raw value of the caller's roles claim
    SUBJECT = "subject"  # the caller's subject (one person or service)
    EVERYONE = "everyone"  # any authenticated querier; value is "*"


EVERYONE_VALUE = "*"


class KnowledgeBaseGrant(Base):
    """Read access to one knowledge base for one principal.

    Grants only ever add access. With authentication on, a knowledge base is
    retrievable by a caller only through a matching grant (or, if the operator
    opts in with AUTH__UNGRANTED_KB_ACCESS=allow, when it has no grants at all).
    """

    __tablename__ = "knowledge_base_grants"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "principal_type", "principal_value", "permission", name="uq_kb_grant"),
        Index("ix_kb_grant_principal", "principal_type", "principal_value"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(
        GUID(), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_type = Column(String, nullable=False)
    principal_value = Column(String, nullable=False)
    permission = Column(String, nullable=False, default="read")
    created_at = Column(DateTime, default=_utcnow)
    created_by = Column(String, nullable=True)

    # The backref keeps knowledge_base.py untouched while making ORM deletes of
    # a knowledge base remove its grants on every backend (the FK's ON DELETE
    # CASCADE covers PostgreSQL; SQLite does not enforce FKs by default).
    knowledge_base = relationship(
        "KnowledgeBase", backref=backref("grants", cascade="all, delete-orphan")
    )
