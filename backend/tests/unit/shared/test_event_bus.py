import uuid
from datetime import UTC, datetime

import pytest

from shared.adapters.event_bus import InMemoryEventBus, UnknownEventVersionError
from shared.domain.outbox import StoredEvent


def _event() -> StoredEvent:
    return StoredEvent(
        event_id=uuid.UUID(int=1),
        event_type="identity.AuthorizationDenied",
        version=1,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor_user_id="actor-1",
        trace_id=None,
        payload={},
    )


async def test_bus_delivers_to_all_handlers() -> None:
    bus = InMemoryEventBus()
    calls: list[str] = []

    async def first(event: StoredEvent) -> None:
        calls.append(f"first:{event.event_type}")

    async def second(event: StoredEvent) -> None:
        calls.append("second")

    bus.register(event_type=_event().event_type, version=1, handler=first)
    bus.register(event_type=_event().event_type, version=1, handler=second)

    await bus.dispatch(_event())
    assert calls == ["first:identity.AuthorizationDenied", "second"]


async def test_bus_rejects_unregistered_version() -> None:
    with pytest.raises(UnknownEventVersionError):
        await InMemoryEventBus().dispatch(_event())


def test_bus_completeness_fails_at_startup() -> None:
    with pytest.raises(RuntimeError, match="handlers missing"):
        InMemoryEventBus().assert_complete([("identity.AuthorizationDenied", 1)])
