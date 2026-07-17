"""PRD-014 transactional outbox acceptance coverage."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from tests.fakes.providers import FixedClock

from shared.adapters.event_bus import InMemoryEventBus
from shared.adapters.event_publisher import PostgresEventPublisher, event_transaction
from shared.adapters.outbox import outbox
from shared.adapters.outbox_relay import OutboxRelay
from shared.domain.events import DomainEvent

pytestmark = pytest.mark.integration


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    event_type: ClassVar[str] = "shared.SomethingHappened"
    subject: str


@pytest.fixture
async def engine(migrated_database: dict[str, str]):
    engine = create_async_engine(migrated_database["asyncpg"])
    yield engine
    await engine.dispose()


async def _publish(factory: async_sessionmaker, event: DomainEvent) -> None:
    async with factory() as session, session.begin(), event_transaction(session):
        await PostgresEventPublisher().publish(event)


async def test_event_is_committed_or_rolled_back_with_its_unit_of_work(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    event = SomethingHappened(
        event_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        actor_user_id="user-1",
        subject="ok",
    )
    await _publish(factory, event)

    async with engine.connect() as connection:
        assert (
            await connection.scalar(select(func.count()).where(outbox.c.event_id == event.event_id))
            == 1
        )

    rolled_back = SomethingHappened(
        event_id=uuid.uuid4(), occurred_at=datetime.now(UTC), subject="rollback"
    )
    with pytest.raises(RuntimeError, match="force rollback"):
        async with factory() as session, session.begin(), event_transaction(session):
            await PostgresEventPublisher().publish(rolled_back)
            raise RuntimeError("force rollback")

    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count()).where(outbox.c.event_id == rolled_back.event_id)
            )
            == 0
        )
    async with engine.begin() as connection:
        await connection.execute(delete(outbox).where(outbox.c.event_id == event.event_id))


async def test_relay_dispatches_and_marks_processed(engine: AsyncEngine) -> None:
    clock = FixedClock()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    event = SomethingHappened(event_id=uuid.uuid4(), occurred_at=clock.now(), subject="deliver")
    await _publish(factory, event)
    bus = InMemoryEventBus()
    delivered: list[uuid.UUID] = []

    async def handler(received) -> None:
        delivered.append(received.event_id)

    bus.register(event_type=event.event_type, version=1, handler=handler)
    relay = OutboxRelay(
        session_factory=factory,
        event_bus=bus,
        clock=clock,
        batch_size=10,
        max_attempts=5,
        max_backoff_seconds=60,
    )

    assert (await relay.run_once()).processed == 1
    assert delivered == [event.event_id]
    async with engine.connect() as connection:
        status = await connection.scalar(
            select(outbox.c.status).where(outbox.c.event_id == event.event_id)
        )
    assert status == "processed"


async def test_poison_event_dead_letters_without_blocking_the_batch(engine: AsyncEngine) -> None:
    clock = FixedClock()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bad = SomethingHappened(event_id=uuid.uuid4(), occurred_at=clock.now(), subject="bad")
    good = SomethingHappened(event_id=uuid.uuid4(), occurred_at=clock.now(), subject="good")
    await _publish(factory, bad)
    await _publish(factory, good)
    bus = InMemoryEventBus()
    delivered: list[uuid.UUID] = []

    async def handler(event) -> None:
        if event.event_id == bad.event_id:
            raise RuntimeError("poison")
        delivered.append(event.event_id)

    bus.register(event_type=bad.event_type, version=1, handler=handler)
    relay = OutboxRelay(
        session_factory=factory,
        event_bus=bus,
        clock=clock,
        batch_size=10,
        max_attempts=5,
        max_backoff_seconds=60,
    )
    for _ in range(5):
        await relay.run_once()
        clock.advance(seconds=60)

    assert delivered == [good.event_id]
    async with engine.connect() as connection:
        status = await connection.scalar(
            select(outbox.c.status).where(outbox.c.event_id == bad.event_id)
        )
    assert status == "failed"


async def test_two_relays_do_not_dispatch_one_row_twice(engine: AsyncEngine) -> None:
    clock = FixedClock()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    event = SomethingHappened(event_id=uuid.uuid4(), occurred_at=clock.now(), subject="race")
    await _publish(factory, event)
    bus = InMemoryEventBus()
    entered = asyncio.Event()
    release = asyncio.Event()
    deliveries = 0

    async def slow_handler(_event) -> None:
        nonlocal deliveries
        deliveries += 1
        entered.set()
        await release.wait()

    bus.register(event_type=event.event_type, version=1, handler=slow_handler)
    kwargs = {
        "session_factory": factory,
        "event_bus": bus,
        "clock": clock,
        "batch_size": 10,
        "max_attempts": 5,
        "max_backoff_seconds": 60,
    }
    first = asyncio.create_task(OutboxRelay(**kwargs).run_once())
    await entered.wait()
    second = await OutboxRelay(**kwargs).run_once()
    release.set()
    first_result = await first

    assert deliveries == 1
    assert first_result.processed + second.processed == 1
