"""Persistence boundary owned by the outbox relay."""

from datetime import datetime
from typing import Protocol

from shared.domain.events import DomainEvent
from shared.domain.outbox import OutboxStats, StoredEvent


class OutboxRepository(Protocol):
    async def add(self, event: DomainEvent) -> None:
        """Add an event to the caller's already-open transaction."""
        ...

    async def claim_pending(self, *, now: datetime, limit: int) -> list[StoredEvent]:
        """Lock a dispatch batch using SKIP LOCKED."""
        ...

    async def mark_processed(self, event_id: object, *, processed_at: datetime) -> None: ...

    async def mark_failed(
        self,
        event_id: object,
        *,
        error: str,
        next_attempt_at: datetime,
        dead_letter: bool,
    ) -> None: ...

    async def stats(self, *, now: datetime) -> OutboxStats: ...
