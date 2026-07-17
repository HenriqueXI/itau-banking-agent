"""PostgreSQL implementation of the insert-only audit log."""

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from audit.adapters.outbound.postgres.tables import audit_events
from audit.application.dto import AuditPage, AuditQuery
from audit.domain.entities import AuditEvent


def _to_event(row: Any) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        event_id=row.event_id,
        user_ref=row.user_ref,
        action=row.action,
        amount=row.amount,
        occurred_at=row.occurred_at,
        resource=row.resource,
        outcome=row.outcome,
        trace_id=row.trace_id,
        details=dict(row.details),
    )


class PostgresAuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> bool:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                insert(audit_events)
                .values(
                    id=event.id,
                    event_id=event.event_id,
                    user_ref=event.user_ref,
                    action=event.action,
                    amount=event.amount,
                    occurred_at=event.occurred_at,
                    resource=event.resource,
                    outcome=event.outcome,
                    trace_id=event.trace_id,
                    details=dict(event.details),
                )
                .on_conflict_do_nothing(index_elements=[audit_events.c.event_id])
            ),
        )
        return result.rowcount > 0

    async def list(self, query: AuditQuery) -> AuditPage:
        conditions = self._conditions(query)
        count = await self._session.scalar(
            select(func.count()).select_from(audit_events).where(*conditions)
        )
        result = await self._session.execute(
            select(audit_events)
            .where(*conditions)
            .order_by(audit_events.c.occurred_at.desc(), audit_events.c.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        return AuditPage(items=tuple(_to_event(row) for row in result.all()), total=int(count or 0))

    async def get_by_id(self, audit_id: uuid.UUID) -> AuditEvent | None:
        result = await self._session.execute(
            select(audit_events).where(audit_events.c.id == audit_id)
        )
        row = result.one_or_none()
        return _to_event(row) if row is not None else None

    @staticmethod
    def _conditions(query: AuditQuery) -> Sequence[Any]:
        conditions: list[Any] = []
        if query.user_ref is not None:
            conditions.append(audit_events.c.user_ref == query.user_ref)
        if query.user_refs is not None:
            conditions.append(audit_events.c.user_ref.in_(query.user_refs))
        if query.action is not None:
            conditions.append(audit_events.c.action == query.action)
        if query.from_at is not None:
            conditions.append(audit_events.c.occurred_at >= query.from_at)
        if query.to_at is not None:
            conditions.append(audit_events.c.occurred_at <= query.to_at)
        return conditions
