from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.models.connector import Connector, ConnectorFileState, FileSyncStatus


class ConnectorRepository:
    """Persistence operations for Connector definitions and their per-file sync state."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, connector: Connector) -> Connector:
        self.session.add(connector)
        self.session.commit()
        self.session.refresh(connector)
        return connector

    def get_by_id(self, connector_id: UUID) -> Optional[Connector]:
        return self.session.get(Connector, connector_id)

    def get_by_name(self, name: str) -> Optional[Connector]:
        return self.session.scalar(select(Connector).where(Connector.name == name))

    def list_all(self) -> List[Connector]:
        return list(self.session.scalars(select(Connector).order_by(Connector.name)))

    def delete(self, connector: Connector) -> None:
        self.session.delete(connector)
        self.session.commit()

    def save(self, connector: Connector) -> Connector:
        self.session.commit()
        return connector

    # -- the run lock ---------------------------------------------------------------------------------------------- #
    def try_lock(self, connector: Connector, stale_after: timedelta, now: Optional[datetime] = None) -> bool:
        """Mark a run as started unless a recent one is still going; False means another run holds the lock."""
        now = now or datetime.now(timezone.utc)
        started = connector.run_started_at
        if started is not None:
            started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
            if now - started < stale_after:
                return False
        connector.run_started_at = now
        self.session.commit()
        return True

    def finish(self, connector: Connector, status: str, summary: Dict[str, Any], now: Optional[datetime] = None) -> None:
        connector.run_started_at = None
        connector.last_run_at = now or datetime.now(timezone.utc)
        connector.last_run_status = status
        connector.last_run_summary = summary
        self.session.commit()

    # -- per-file state -------------------------------------------------------------------------------------------- #
    def states(self, connector_id: UUID) -> Dict[str, ConnectorFileState]:
        stmt = select(ConnectorFileState).where(ConnectorFileState.connector_id == connector_id)
        return {state.source_key: state for state in self.session.scalars(stmt)}

    def get_state(self, connector_id: UUID, source_key: str) -> Optional[ConnectorFileState]:
        stmt = select(ConnectorFileState).where(
            ConnectorFileState.connector_id == connector_id, ConnectorFileState.source_key == source_key
        )
        return self.session.scalar(stmt)

    def add_state(self, state: ConnectorFileState) -> ConnectorFileState:
        self.session.add(state)
        self.session.commit()
        return state

    def delete_state(self, state: ConnectorFileState) -> None:
        self.session.delete(state)
        self.session.commit()

    def file_counts(self, connector_id: UUID) -> Dict[str, int]:
        stmt = (
            select(ConnectorFileState.status, func.count())
            .where(ConnectorFileState.connector_id == connector_id)
            .group_by(ConnectorFileState.status)
        )
        counts = {status.value: 0 for status in FileSyncStatus}
        counts.update({status: count for status, count in self.session.execute(stmt)})
        return counts
