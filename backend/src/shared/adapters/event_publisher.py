"""Log-only EventPublisher — the seam until the transactional outbox (PRD-014).

Events are logged through structlog, so payloads pass the shared PII masking
processor. Replaced by the outbox writer without touching use cases.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from shared.adapters.outbox import PostgresOutboxRepository
from shared.domain.events import DomainEvent

logger = structlog.get_logger(__name__)
_event_session: ContextVar[AsyncSession | None] = ContextVar("event_session", default=None)


def current_event_session() -> AsyncSession:
    """Return the request transaction bound by :func:`event_transaction`.

    Application adapters that persist a business aggregate must share the
    transaction used by the outbox writer; silently opening another session
    would break ADR-006's atomicity guarantee.
    """
    session = _event_session.get()
    if session is None or not session.in_transaction():
        raise RuntimeError("Banking workflows must run inside an event transaction")
    return session


@asynccontextmanager
async def event_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """Bind an already-open unit of work to event publishing for this task."""
    token = _event_session.set(session)
    try:
        yield
    finally:
        _event_session.reset(token)


class LoggingEventPublisher:
    async def publish(self, event: DomainEvent) -> None:
        logger.info(
            "domain_event.raised",
            event_type=event.event_type,
            event_id=str(event.event_id),
            trace_id=event.trace_id,
            payload=event.payload(),
        )


class PostgresEventPublisher:
    """Writes through the ambient transaction and refuses implicit commits."""

    async def publish(self, event: DomainEvent) -> None:
        session = _event_session.get()
        if session is None or not session.in_transaction():
            raise RuntimeError("Domain events must be published inside a transaction")
        await PostgresOutboxRepository(session).add(event)
