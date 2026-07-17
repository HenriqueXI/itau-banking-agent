"""Application handler for one at-least-once outbox delivery."""

from audit.application.event_mapper import map_event
from audit.application.ports.audit_log_repository import AuditLogRepository
from shared.application.ports.id_generator import IdGenerator
from shared.domain.outbox import StoredEvent


class ConsumeAuditEvent:
    def __init__(self, *, repository: AuditLogRepository, id_generator: IdGenerator) -> None:
        self._repository = repository
        self._ids = id_generator

    async def execute(self, event: StoredEvent) -> bool:
        return await self._repository.append(map_event(event, id_generator=self._ids))
