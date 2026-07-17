import uuid
from datetime import UTC, datetime

import pytest

from shared.adapters.event_publisher import PostgresEventPublisher
from shared.domain.events import DomainEvent


class _Event(DomainEvent):
    event_type = "shared.Tested"


async def test_postgres_publisher_rejects_missing_unit_of_work() -> None:
    event = _Event(event_id=uuid.UUID(int=1), occurred_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(RuntimeError, match="inside a transaction"):
        await PostgresEventPublisher().publish(event)
