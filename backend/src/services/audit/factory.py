from src.config import Settings
from src.db.interfaces.base import BaseDatabase

from .recorder import AuditRecorder, DatabaseAuditRecorder, NoopAuditRecorder


def make_audit_recorder(settings: Settings, database: BaseDatabase) -> AuditRecorder:
    """Build the recorder AUDIT__LEVEL asks for; ``off`` never touches the database."""
    audit = settings.audit
    if audit.level == "off":
        return NoopAuditRecorder()
    return DatabaseAuditRecorder(database.get_session, level=audit.level, fail_mode=audit.fail_mode)
