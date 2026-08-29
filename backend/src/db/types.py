"""Cross-dialect column types shared by the domain models.

The platform targets PostgreSQL in production, but the test-suite and local
tooling benefit from being able to run the exact same ORM models against
SQLite. SQLAlchemy's ``postgresql.UUID`` type is dialect-specific and breaks on
SQLite, so we provide a portable ``GUID`` type that stores a native ``UUID`` on
PostgreSQL and a 36-character string elsewhere.
"""
import uuid
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native ``UUID`` type when available and falls back to a
    ``CHAR(36)`` string representation for all other dialects (e.g. SQLite).
    Values are always returned to Python as :class:`uuid.UUID` instances.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))

    def process_result_value(self, value: Any, dialect: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
