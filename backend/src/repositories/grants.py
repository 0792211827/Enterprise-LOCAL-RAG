from typing import Iterable, List, Optional, Set
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session
from src.models.access import EVERYONE_VALUE, GrantPrincipalType, KnowledgeBaseGrant


class GrantRepository:
    """Persistence operations for knowledge-base grants."""

    def __init__(self, session: Session):
        self.session = session

    def list_for_knowledge_base(self, kb_id: UUID) -> List[KnowledgeBaseGrant]:
        stmt = (
            select(KnowledgeBaseGrant)
            .where(KnowledgeBaseGrant.knowledge_base_id == kb_id)
            .order_by(KnowledgeBaseGrant.principal_type, KnowledgeBaseGrant.principal_value)
        )
        return list(self.session.scalars(stmt))

    def replace_for_knowledge_base(
        self,
        kb_id: UUID,
        entries: Iterable[tuple[str, str]],
        created_by: Optional[str] = None,
    ) -> List[KnowledgeBaseGrant]:
        """Replace a knowledge base's grants with ``(principal_type, value)`` pairs.

        Replacement (rather than add/remove calls) keeps the admin's intent
        atomic: the saved set is exactly what was submitted.
        """
        unique = sorted(set(entries))
        self.session.execute(delete(KnowledgeBaseGrant).where(KnowledgeBaseGrant.knowledge_base_id == kb_id))
        for principal_type, value in unique:
            self.session.add(
                KnowledgeBaseGrant(
                    knowledge_base_id=kb_id,
                    principal_type=principal_type,
                    principal_value=value,
                    permission="read",
                    created_by=created_by,
                )
            )
        self.session.commit()
        return self.list_for_knowledge_base(kb_id)

    def readable_knowledge_base_ids(
        self,
        subject: str,
        groups: Iterable[str],
        roles: Iterable[str],
    ) -> Set[str]:
        """IDs of knowledge bases any grant makes readable for this identity."""
        clauses = [
            KnowledgeBaseGrant.principal_type == GrantPrincipalType.EVERYONE.value,
            and_(
                KnowledgeBaseGrant.principal_type == GrantPrincipalType.SUBJECT.value,
                KnowledgeBaseGrant.principal_value == subject,
            ),
        ]
        groups, roles = list(groups), list(roles)
        if groups:
            clauses.append(
                and_(
                    KnowledgeBaseGrant.principal_type == GrantPrincipalType.GROUP.value,
                    KnowledgeBaseGrant.principal_value.in_(groups),
                )
            )
        if roles:
            clauses.append(
                and_(
                    KnowledgeBaseGrant.principal_type == GrantPrincipalType.ROLE.value,
                    KnowledgeBaseGrant.principal_value.in_(roles),
                )
            )
        stmt = select(KnowledgeBaseGrant.knowledge_base_id).where(
            KnowledgeBaseGrant.permission == "read", or_(*clauses)
        )
        return {str(kb_id) for kb_id in self.session.scalars(stmt)}

    def granted_knowledge_base_ids(self) -> Set[str]:
        """IDs of knowledge bases that have at least one grant."""
        stmt = select(KnowledgeBaseGrant.knowledge_base_id).distinct()
        return {str(kb_id) for kb_id in self.session.scalars(stmt)}


__all__ = ["GrantRepository", "EVERYONE_VALUE"]
