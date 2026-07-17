"""Fake implementations of shared ports (NFR-7) — deterministic, in-memory."""

import uuid
from datetime import UTC, datetime

from shared.domain.events import DomainEvent


class FixedClock:
    """Clock frozen at a known instant; `advance` shifts it explicitly."""

    def __init__(self, instant: datetime | None = None) -> None:
        self._now = instant or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **timedelta_kwargs: float) -> None:
        from datetime import timedelta

        self._now += timedelta(**timedelta_kwargs)


class SequentialIdGenerator:
    """Deterministic UUIDs: 00000000-...-0001, -0002, ..."""

    def __init__(self) -> None:
        self._counter = 0

    def new_id(self) -> uuid.UUID:
        self._counter += 1
        return uuid.UUID(int=self._counter)


class RecordingEventPublisher:
    """Captures published events for assertions."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
