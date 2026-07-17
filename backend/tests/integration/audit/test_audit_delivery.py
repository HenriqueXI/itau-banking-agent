"""Audit persistence and outbox-to-audit delivery against PostgreSQL."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from tests.fakes.providers import FixedClock

from api.event_wiring import build_event_bus
from audit.adapters.outbound.postgres.audit_log_repository import PostgresAuditLogRepository
from audit.adapters.outbound.postgres.tables import audit_events
from audit.application.dto import AuditQuery
from audit.domain.entities import AuditEvent
from identity_access.domain.events import AuthorizationDenied
from shared.adapters.event_publisher import PostgresEventPublisher, event_transaction
from shared.adapters.id_generator import UuidIdGenerator
from shared.adapters.outbox import outbox
from shared.adapters.outbox_relay import OutboxRelay
from shared.domain.events import DomainEvent

pytestmark = pytest.mark.integration


@dataclass(frozen=True, kw_only=True)
class UnknownAuditEvent(DomainEvent):
    event_type: ClassVar[str] = "shared.UnknownAuditEvent"
    value: str


@pytest.fixture
async def engine(migrated_database: dict[str, str]):
    engine = create_async_engine(migrated_database["asyncpg"])
    yield engine
    await engine.dispose()


async def test_repository_deduplicates_and_filters_ordered_rows(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    first = AuditEvent(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        user_ref="system",
        action="STEP_UP",
        amount=None,
        occurred_at=now,
        resource="operation:1",
        outcome="failed",
        trace_id=None,
        details={"reason": "invalid"},
    )
    second = AuditEvent(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        user_ref="system",
        action="STEP_UP",
        amount=Decimal("10.00"),
        occurred_at=now + timedelta(seconds=1),
        resource="operation:2",
        outcome="succeeded",
        trace_id=None,
        details={},
    )
    async with factory() as session, session.begin():
        repository = PostgresAuditLogRepository(session)
        assert await repository.append(first)
        assert not await repository.append(first)
        assert await repository.append(second)

    async with factory() as session:
        page = await PostgresAuditLogRepository(session).list(
            AuditQuery(user_ref="system", action="STEP_UP", page_size=10)
        )
    matching = tuple(
        item for item in page.items if item.event_id in {first.event_id, second.event_id}
    )
    assert tuple(item.id for item in matching) == (second.id, first.id)
    assert all(item.trace_id is None for item in matching)


async def test_relay_creates_one_audit_row_for_redelivery(engine: AsyncEngine) -> None:
    clock = FixedClock()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source = AuthorizationDenied(
        event_id=uuid.uuid4(),
        occurred_at=clock.now(),
        actor_user_id="user-1",
        attempted_action="view_audit",
        target_resource=None,
        reason="role_forbidden",
    )
    async with factory() as session, session.begin(), event_transaction(session):
        await PostgresEventPublisher().publish(source)

    relay = OutboxRelay(
        session_factory=factory,
        event_bus=build_event_bus(session_factory=factory, id_generator=UuidIdGenerator()),
        clock=clock,
        batch_size=10,
        max_attempts=5,
        max_backoff_seconds=60,
    )
    assert (await relay.run_once()).processed >= 1

    # Simulate a crash after handler commit but before the relay's processed mark:
    # at-least-once delivery must remain one audit row by source event_id.
    async with engine.begin() as connection:
        await connection.execute(
            update(outbox)
            .where(outbox.c.event_id == source.event_id)
            .values(status="pending", processed_at=None, next_attempt_at=clock.now())
        )
    await relay.run_once()

    async with engine.connect() as connection:
        count = await connection.scalar(
            select(func.count()).where(audit_events.c.event_id == source.event_id)
        )
    assert count == 1


async def test_unmapped_event_is_dead_lettered(engine: AsyncEngine) -> None:
    clock = FixedClock()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source = UnknownAuditEvent(event_id=uuid.uuid4(), occurred_at=clock.now(), value="poison")
    async with factory() as session, session.begin(), event_transaction(session):
        await PostgresEventPublisher().publish(source)

    relay = OutboxRelay(
        session_factory=factory,
        event_bus=build_event_bus(session_factory=factory, id_generator=UuidIdGenerator()),
        clock=clock,
        batch_size=10,
        max_attempts=1,
        max_backoff_seconds=60,
    )
    assert (await relay.run_once()).failed == 1
    async with engine.connect() as connection:
        status = await connection.scalar(
            select(outbox.c.status).where(outbox.c.event_id == source.event_id)
        )
    assert status == "failed"
