"""EventPublisher port: implemented by the transactional outbox writer (ADR-006)."""

from typing import Protocol

from shared.domain.events import DomainEvent


class EventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None:
        """Record the event in the same transaction as the state change."""
        ...
