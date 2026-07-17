import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from shared.domain.events import DomainEvent
from tests.fakes import FixedClock, SequentialIdGenerator


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    event_type: ClassVar[str] = "shared.SomethingHappened"
    subject: str


def _make_event(subject: str = "s-1") -> SomethingHappened:
    clock, ids = FixedClock(), SequentialIdGenerator()
    return SomethingHappened(event_id=ids.new_id(), occurred_at=clock.now(), subject=subject)


def test_event_carries_envelope_fields() -> None:
    event = _make_event()
    assert event.event_id == uuid.UUID(int=1)
    assert event.occurred_at.tzinfo is not None
    assert event.event_type == "shared.SomethingHappened"


def test_payload_excludes_envelope() -> None:
    assert _make_event(subject="s-2").payload() == {"subject": "s-2"}


def test_event_envelope_is_versioned_and_masks_payload() -> None:
    event = _make_event(subject="ana@example.com")

    assert event.envelope() == {
        "event_id": str(uuid.UUID(int=1)),
        "event_type": "shared.SomethingHappened",
        "version": 1,
        "occurred_at": event.occurred_at.isoformat(),
        "actor_user_id": None,
        "trace_id": None,
        "payload": {"subject": "ana****"},
    }


def test_events_are_immutable() -> None:
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        _make_event().subject = "tampered"  # type: ignore[misc]


def test_payload_serializes_non_json_domain_values() -> None:
    @dataclass(frozen=True, kw_only=True)
    class RichEvent(DomainEvent):
        event_type: ClassVar[str] = "shared.Rich"
        challenge_id: uuid.UUID
        expires_at: datetime

    event = RichEvent(
        event_id=uuid.UUID(int=1),
        occurred_at=FixedClock().now(),
        challenge_id=uuid.UUID(int=2),
        expires_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert event.payload() == {
        "challenge_id": str(uuid.UUID(int=2)),
        "expires_at": "2026-01-01T00:00:00+00:00",
    }
