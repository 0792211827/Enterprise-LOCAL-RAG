"""Shared helpers for audit tests: an in-memory SQLite audit store."""

from contextlib import contextmanager

import src.models  # noqa: F401  (register models on Base.metadata)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.db.interfaces.postgresql import Base
from src.models import AuditEvent


def sqlite_session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def opener(factory: sessionmaker):
    """A ``database.get_session``-shaped callable over ``factory``."""

    @contextmanager
    def open_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return open_session


def events(factory: sessionmaker) -> list[AuditEvent]:
    with factory() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
