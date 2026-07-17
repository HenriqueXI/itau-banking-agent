"""Read-only audit query use case."""

import uuid

from audit.application.dto import AuditPage, AuditQuery
from audit.application.ports.audit_log_repository import AuditLogRepository
from audit.domain.entities import AuditEvent


class QueryAudit:
    def __init__(self, *, repository: AuditLogRepository) -> None:
        self._repository = repository

    @staticmethod
    def validate(query: AuditQuery) -> None:
        if query.page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= query.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if query.from_at is not None and query.to_at is not None and query.from_at > query.to_at:
            raise ValueError("from must not be after to")

    async def list(self, query: AuditQuery) -> AuditPage:
        self.validate(query)
        return await self._repository.list(query)

    async def get(self, audit_id: uuid.UUID) -> AuditEvent | None:
        return await self._repository.get_by_id(audit_id)
