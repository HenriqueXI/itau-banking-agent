"""Persistence port for the immutable audit trail."""

import uuid
from typing import Protocol

from audit.application.dto import AuditPage, AuditQuery
from audit.domain.entities import AuditEvent


class AuditLogRepository(Protocol):
    async def append(self, event: AuditEvent) -> bool:
        """Append once; return false when an outbox redelivery was already recorded."""
        ...

    async def list(self, query: AuditQuery) -> AuditPage: ...

    async def get_by_id(self, audit_id: uuid.UUID) -> AuditEvent | None: ...
