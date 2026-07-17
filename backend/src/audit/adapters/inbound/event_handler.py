"""Outbox bus adapter that persists audit records in its own transaction."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit.adapters.outbound.postgres.audit_log_repository import PostgresAuditLogRepository
from audit.application.use_cases.consume_audit_event import ConsumeAuditEvent
from shared.application.ports.id_generator import IdGenerator
from shared.domain.outbox import StoredEvent


class AuditEventHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        id_generator: IdGenerator,
    ) -> None:
        self._sessions = session_factory
        self._ids = id_generator

    async def handle(self, event: StoredEvent) -> None:
        # A handler transaction is independent from the already-committed producer
        # transaction. Exceptions deliberately reach the relay for retry/dead-letter.
        async with self._sessions() as session, session.begin():
            use_case = ConsumeAuditEvent(
                repository=PostgresAuditLogRepository(session), id_generator=self._ids
            )
            await use_case.execute(event)
