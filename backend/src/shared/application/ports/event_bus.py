"""In-process event bus boundary (ADR-006)."""

from typing import Protocol

from shared.domain.outbox import StoredEvent


class EventBus(Protocol):
    async def dispatch(self, event: StoredEvent) -> None:
        """Deliver an event to every registered handler or raise a typed failure."""
        ...
