"""PIX execution interleavings against real PostgreSQL row locks and the real
MCP protocol client + simulator: exactly-once under races, rollback replay,
crash-marker recovery, confirm-vs-expire, and BR-3.2 bucket arithmetic.

These are the PRD-008 "first-class deliverables" — the tests that prove money
cannot move twice under any of the failure shapes we know how to provoke.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from banking.adapters.outbound.mcp_client import McpBankingSystemsClient
from banking.adapters.outbound.postgres.pending_operation_repository import (
    PostgresPendingOperationRepository,
    PostgresPixTransferRepository,
)
from banking.adapters.outbound.postgres.tables import pix_daily_buckets
from banking.application.use_cases.execute_pix_transfer import ExecutePixTransferUseCase
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.pix import PixTransfer
from mcp_server.simulator import CoreBankingSimulator
from shared.adapters.clock import SystemClock
from shared.container import Container
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator
from tests.integration.banking.conftest import ANA_ID, McpServerHandle

pytestmark = pytest.mark.integration

PIX_KEY = "maria@exemplo.com"
DAILY_LIMIT = Decimal("50000")


class _SimulatedCrashError(Exception):
    pass


def _operation(amount: str, *, created_at: datetime, status: OperationStatus) -> PendingOperation:
    from dataclasses import replace

    operation = PendingOperation.create(
        operation_id=uuid.uuid4(),
        user_id=ANA_ID,
        tool="fazer_pix",
        params={
            "customer_id": "123",
            "account_id": "acc-1",
            "recipient_key": PIX_KEY,
            "amount": amount,
            "salt": str(uuid.uuid4()),  # unique hash per test run
        },
        tier=3,
        now=created_at,
        ttl=timedelta(minutes=5),
    )
    return replace(operation, status=status)


async def _seed(container: Container, operation: PendingOperation) -> None:
    async with container.session_factory() as session, session.begin():
        await PostgresPendingOperationRepository(session).add(operation)
        transfer = PixTransfer.create(
            operation_hash=operation.operation_hash,
            customer_id="123",
            account_id="acc-1",
            recipient_key=PIX_KEY,
            amount=Decimal(str(operation.params["amount"])),
            local_day=operation.created_at.date(),
        )
        reserved = await PostgresPixTransferRepository(session).reserve(
            transfer, daily_limit=DAILY_LIMIT
        )
        assert reserved is None


def _executor(session, client, clock) -> ExecutePixTransferUseCase:
    return ExecutePixTransferUseCase(
        operations=PostgresPendingOperationRepository(session),
        transfers=PostgresPixTransferRepository(session),
        banking=client,
        events=RecordingEventPublisher(),
        clock=clock,
        id_generator=SequentialIdGenerator(),
    )


async def _operation_status(container: Container, operation_hash: str) -> str:
    async with container.session_factory() as session:
        operation = await PostgresPendingOperationRepository(session).get(operation_hash)
    assert operation is not None
    return operation.status.value


async def _bucket(
    container: Container, customer_id: str, local_day: date
) -> tuple[Decimal, Decimal]:
    async with container.engine.connect() as connection:
        row = (
            await connection.execute(
                select(pix_daily_buckets).where(
                    and_(
                        pix_daily_buckets.c.customer_id == customer_id,
                        pix_daily_buckets.c.local_day == local_day,
                    )
                )
            )
        ).one_or_none()
    if row is None:
        return Decimal("0"), Decimal("0")
    return Decimal(str(row.pending_amount)), Decimal(str(row.executed_amount))


async def test_concurrent_confirms_serialize_on_the_row_lock(
    container: Container,
    mcp_server: McpServerHandle,
    simulator: CoreBankingSimulator,
) -> None:
    now = datetime.now(UTC)
    operation = _operation("200.00", created_at=now, status=OperationStatus.PENDING_CONFIRMATION)
    await _seed(container, operation)

    async def confirm() -> object:
        engine = create_async_engine(container.settings.database_url)
        client = McpBankingSystemsClient(url=mcp_server.url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session, session.begin():
                return await _executor(session, client, SystemClock()).execute(
                    operation_hash=operation.operation_hash, user_id=ANA_ID
                )
        finally:
            await client.aclose()
            await engine.dispose()

    results = await asyncio.gather(confirm(), confirm())

    assert simulator.pix_executions == 1
    assert await _operation_status(container, operation.operation_hash) == "executed"
    receipts = [r for r in results if r is not None]
    assert receipts, "at least the winning confirm returns the receipt"


async def test_crash_after_mcp_success_before_commit_replays_exactly_once(
    container: Container,
    mcp_server: McpServerHandle,
    simulator: CoreBankingSimulator,
) -> None:
    """The worst interleaving: MCP executed, our transaction rolled back. The
    replay must converge on one PIX via the idempotency key."""
    now = datetime.now(UTC)
    operation = _operation("300.00", created_at=now, status=OperationStatus.PENDING_CONFIRMATION)
    await _seed(container, operation)
    client = McpBankingSystemsClient(url=mcp_server.url)

    try:
        with pytest.raises(_SimulatedCrashError):
            async with container.session_factory() as session, session.begin():
                receipt = await _executor(session, client, SystemClock()).execute(
                    operation_hash=operation.operation_hash, user_id=ANA_ID
                )
                assert receipt is not None
                raise _SimulatedCrashError()

        assert simulator.pix_executions == 1
        assert (
            await _operation_status(container, operation.operation_hash) == "pending_confirmation"
        )

        async with container.session_factory() as session, session.begin():
            retried = await _executor(session, client, SystemClock()).execute(
                operation_hash=operation.operation_hash, user_id=ANA_ID
            )
    finally:
        await client.aclose()

    assert retried is not None
    assert simulator.pix_executions == 1  # deduped by the idempotency key
    assert await _operation_status(container, operation.operation_hash) == "executed"


async def test_committed_executing_marker_recovers_via_the_idempotency_key(
    container: Container,
    mcp_server: McpServerHandle,
    simulator: CoreBankingSimulator,
) -> None:
    """workflows.md §4: a crash that persisted EXECUTING is replayed, not stuck."""
    now = datetime.now(UTC)
    operation = _operation("250.00", created_at=now, status=OperationStatus.EXECUTING)
    await _seed(container, operation)
    client = McpBankingSystemsClient(url=mcp_server.url)

    try:
        async with container.session_factory() as session, session.begin():
            receipt = await _executor(session, client, SystemClock()).execute(
                operation_hash=operation.operation_hash, user_id=ANA_ID
            )
    finally:
        await client.aclose()

    assert receipt is not None
    assert simulator.pix_executions == 1
    assert await _operation_status(container, operation.operation_hash) == "executed"


async def test_confirm_racing_expiry_expires_and_releases_the_reservation(
    container: Container,
    mcp_server: McpServerHandle,
    simulator: CoreBankingSimulator,
) -> None:
    created = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    operation = _operation(
        "400.00", created_at=created, status=OperationStatus.PENDING_CONFIRMATION
    )
    await _seed(container, operation)
    local_day = created.date()
    pending_before, executed_before = await _bucket(container, "123", local_day)
    assert pending_before == Decimal("400.00")
    clock = FixedClock(created + timedelta(minutes=6))
    client = McpBankingSystemsClient(url=mcp_server.url)

    try:
        async with container.session_factory() as session, session.begin():
            events = RecordingEventPublisher()
            executor = ExecutePixTransferUseCase(
                operations=PostgresPendingOperationRepository(session),
                transfers=PostgresPixTransferRepository(session),
                banking=client,
                events=events,
                clock=clock,
                id_generator=SequentialIdGenerator(),
            )
            receipt = await executor.execute(
                operation_hash=operation.operation_hash, user_id=ANA_ID
            )
    finally:
        await client.aclose()

    assert receipt is None
    assert simulator.pix_executions == 0
    assert await _operation_status(container, operation.operation_hash) == "expired"
    assert [e.event_type for e in events.events] == ["banking.OperationConfirmationExpired"]
    pending_after, executed_after = await _bucket(container, "123", local_day)
    assert pending_after == Decimal("0.00")  # the 400.00 reservation was released
    assert executed_after == executed_before


async def test_daily_limit_arithmetic_counts_pending_and_respects_the_boundary(
    container: Container,
) -> None:
    """BR-3.2 edge cases on the real bucket SQL: pending counts, the exact
    boundary passes, one cent over is denied, release frees headroom."""
    day = date(2026, 7, 2)
    limit = Decimal("5000")

    def transfer(amount: str) -> PixTransfer:
        return PixTransfer.create(
            operation_hash=f"races-bucket-{amount}-{uuid.uuid4()}",
            customer_id="999",
            account_id="acc-9",
            recipient_key=PIX_KEY,
            amount=Decimal(amount),
            local_day=day,
        )

    first = transfer("3000.00")
    async with container.session_factory() as session, session.begin():
        repository = PostgresPixTransferRepository(session)
        assert await repository.reserve(first, daily_limit=limit) is None
        # Exact boundary: 3000 pending + 2000 == 5000 → allowed.
        assert await repository.reserve(transfer("2000.00"), daily_limit=limit) is None
        # One cent over → denied, stating the remaining amount.
        assert await repository.reserve(transfer("0.01"), daily_limit=limit) == Decimal("0.00")
        # Releasing the first reservation frees headroom again.
        await repository.release(first.operation_hash)
        assert await repository.reserve(transfer("0.01"), daily_limit=limit) is None
