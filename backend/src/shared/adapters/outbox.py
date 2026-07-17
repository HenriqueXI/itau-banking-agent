"""SQLAlchemy implementations for transactional event persistence."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    Table,
    Text,
    and_,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from shared.adapters.database import metadata
from shared.domain.events import DomainEvent
from shared.domain.outbox import OutboxStats, StoredEvent
from shared.logging.masking import mask_pii

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("event_type", Text, nullable=False),
    Column("event_version", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_user_id", Text, nullable=True),
    Column("trace_id", Text, nullable=True),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_error", Text, nullable=True),
    Column("processed_at", DateTime(timezone=True), nullable=True),
)


def _stored(row: Any) -> StoredEvent:
    return StoredEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        version=row.event_version,
        occurred_at=row.occurred_at,
        actor_user_id=row.actor_user_id,
        trace_id=row.trace_id,
        payload=dict(row.payload),
        attempts=row.attempts,
    )


class PostgresOutboxRepository:
    """Repository deliberately bound to one SQLAlchemy session/unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: DomainEvent) -> None:
        if not self._session.in_transaction():
            raise RuntimeError("Domain events must be published inside a transaction")
        await self._session.execute(
            insert(outbox).values(
                event_id=event.event_id,
                event_type=event.event_type,
                event_version=event.version,
                occurred_at=event.occurred_at,
                actor_user_id=event.actor_user_id,
                trace_id=event.trace_id,
                payload=event.payload(),
                next_attempt_at=event.occurred_at,
            )
        )

    async def claim_pending(self, *, now: datetime, limit: int) -> list[StoredEvent]:
        result = await self._session.execute(
            select(outbox)
            .where(and_(outbox.c.status == "pending", outbox.c.next_attempt_at <= now))
            .order_by(outbox.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_stored(row) for row in result.all()]

    async def mark_processed(self, event_id: object, *, processed_at: datetime) -> None:
        await self._session.execute(
            update(outbox)
            .where(outbox.c.event_id == event_id)
            .values(status="processed", processed_at=processed_at, last_error=None)
        )

    async def mark_failed(
        self,
        event_id: object,
        *,
        error: str,
        next_attempt_at: datetime,
        dead_letter: bool,
    ) -> None:
        await self._session.execute(
            update(outbox)
            .where(outbox.c.event_id == event_id)
            .values(
                status="failed" if dead_letter else "pending",
                attempts=outbox.c.attempts + 1,
                next_attempt_at=next_attempt_at,
                last_error=mask_pii(error)[:1000],
            )
        )

    async def stats(self, *, now: datetime) -> OutboxStats:
        result = await self._session.execute(
            select(
                func.count().filter(outbox.c.status == "pending"),
                func.count().filter(outbox.c.status == "failed"),
                func.min(outbox.c.occurred_at).filter(outbox.c.status == "pending"),
            )
        )
        pending, failed, oldest = result.one()
        return OutboxStats(
            pending_count=int(pending), failed_count=int(failed), oldest_pending_at=oldest
        )
