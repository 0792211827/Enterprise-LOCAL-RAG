from typing import Dict, Iterable, List, Optional, Set
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from src.config import AuthSettings
from src.models.access import (
    EVERYONE_VALUE,
    DocumentRestriction,
    GrantPrincipalType,
    KnowledgeBaseGrant,
    principal_key,
)
from src.models.audit import AuditEvent
from src.models.document import Document

KNOWN_VALUE_SCAN_LIMIT = 5000  # recent audit events read when looking for claim values the platform has seen


class RestrictionRepository:
    """Persistence operations for Document restrictions."""

    def __init__(self, session: Session):
        self.session = session

    def list_for_document(self, document_id: UUID) -> List[DocumentRestriction]:
        stmt = (
            select(DocumentRestriction)
            .where(DocumentRestriction.document_id == document_id)
            .order_by(DocumentRestriction.principal_type, DocumentRestriction.principal_value)
        )
        return list(self.session.scalars(stmt))

    def keys_for_document(self, document_id: UUID) -> List[str]:
        """The document's restriction Principals as chunk keywords; empty when it is unrestricted."""
        return sorted(principal_key(r.principal_type, r.principal_value) for r in self.list_for_document(document_id))

    def list_for_knowledge_base(self, kb_id: UUID) -> Dict[str, List[DocumentRestriction]]:
        """Restrictions of every Document in a Knowledge Base, by document ID (unrestricted Documents are absent)."""
        stmt = (
            select(DocumentRestriction)
            .join(Document, Document.id == DocumentRestriction.document_id)
            .where(Document.knowledge_base_id == kb_id)
            .order_by(DocumentRestriction.principal_type, DocumentRestriction.principal_value)
        )
        grouped: Dict[str, List[DocumentRestriction]] = {}
        for row in self.session.scalars(stmt):
            grouped.setdefault(str(row.document_id), []).append(row)
        return grouped

    def replace_for_document(
        self, document_id: UUID, entries: Iterable[tuple[str, str]], created_by: Optional[str] = None
    ) -> List[DocumentRestriction]:
        """Replace a Document's restrictions with exactly ``(principal_type, value)`` pairs; none clears them."""
        self.session.execute(delete(DocumentRestriction).where(DocumentRestriction.document_id == document_id))
        for principal_type, value in sorted(set(entries)):
            self.session.add(
                DocumentRestriction(
                    document_id=document_id, principal_type=principal_type, principal_value=value, created_by=created_by
                )
            )
        self.session.commit()
        return self.list_for_document(document_id)

    def known_values(self, auth: AuthSettings) -> Dict[str, Set[str]]:
        """Claim values the platform has seen, by principal type: those named by Grants, configured as role mappings,
        or carried by a caller (subject, groups) in the audit log. A restriction naming none of them is probably a typo."""
        known: Dict[str, Set[str]] = {
            GrantPrincipalType.GROUP.value: set(),
            GrantPrincipalType.ROLE.value: set(auth.admin_role_values) | set(auth.query_role_values) | set(auth.auditor_role_values),
            GrantPrincipalType.SUBJECT.value: set(),
            GrantPrincipalType.EVERYONE.value: {EVERYONE_VALUE},
        }
        for principal_type, value in self.session.execute(
            select(KnowledgeBaseGrant.principal_type, KnowledgeBaseGrant.principal_value).distinct()
        ):
            known.setdefault(principal_type, set()).add(value)

        recent = self.session.execute(
            select(AuditEvent.actor_subject, AuditEvent.actor_groups)
            .where(AuditEvent.actor_subject.is_not(None))
            .order_by(AuditEvent.occurred_at.desc())
            .limit(KNOWN_VALUE_SCAN_LIMIT)
        )
        for subject, groups in recent:
            known[GrantPrincipalType.SUBJECT.value].add(subject)
            known[GrantPrincipalType.GROUP.value].update(str(g) for g in groups or [])
        return known
