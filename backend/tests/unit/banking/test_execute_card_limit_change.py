"""ExecuteCardLimitChangeUseCase: exactly-once execution, honest failure,
expiry-at-execute and crash recovery (workflows.md §4)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from banking.application.use_cases.execute_card_limit_change import ExecuteCardLimitChangeUseCase
from banking.domain.errors import SystemUnavailable
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.values import LimitUpdateCommand, LimitUpdateReceipt

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
USER_ID = uuid.UUID(int=1)


class OperationsStore:
    """In-memory PendingOperationRepository — keyed by operation_hash."""

    def __init__(self, *operations: PendingOperation) -> None:
        self.items = {op.operation_hash: op for op in operations}

    async def get_for_user(
        self, operation_hash: str, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None:
        operation = self.items.get(operation_hash)
        if operation is None or operation.user_id != user_id:
            return None
        return operation

    async def save(self, operation: PendingOperation) -> None:
        self.items[operation.operation_hash] = operation


class RecordingBanking:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.commands: list[LimitUpdateCommand] = []
        self._fail = fail

    async def update_card_limit(self, command: LimitUpdateCommand) -> LimitUpdateReceipt:
        self.commands.append(command)
        if self._fail is not None:
            raise self._fail
        return LimitUpdateReceipt(
            card_id=command.card_id,
            old_limit=Decimal("5000"),
            new_limit=command.new_limit,
            updated_at=NOW,
        )


def pending_operation(*, ttl_minutes: int = 5) -> PendingOperation:
    return PendingOperation.create(
        operation_id=uuid.UUID(int=99),
        user_id=USER_ID,
        tool="alterar_limite",
        params={
            "customer_id": "123",
            "card_id": "card-1",
            "current_limit": "5000",
            "new_limit": Decimal("15000"),
            "last4": "4242",
        },
        tier=2,
        now=NOW,
        ttl=timedelta(minutes=ttl_minutes),
    )


def use_case(
    operations: OperationsStore,
    banking: RecordingBanking,
    events: RecordingEventPublisher,
    clock: FixedClock,
) -> ExecuteCardLimitChangeUseCase:
    return ExecuteCardLimitChangeUseCase(
        operations=operations,
        banking=banking,
        events=events,
        clock=clock,
        id_generator=SequentialIdGenerator(),
    )


async def test_confirmed_operation_executes_once_with_the_bound_idempotency_key() -> None:
    operation = pending_operation()
    operations = OperationsStore(operation)
    banking, events = RecordingBanking(), RecordingEventPublisher()

    receipt = await use_case(operations, banking, events, FixedClock(NOW)).execute(
        operation_hash=operation.operation_hash, user_id=USER_ID
    )

    assert receipt is not None and receipt.new_limit == Decimal("15000")
    assert [c.idempotency_key for c in banking.commands] == [operation.operation_hash]
    assert operations.items[operation.operation_hash].status is OperationStatus.EXECUTED
    assert [e.event_type for e in events.events] == ["banking.CardLimitChanged"]


async def test_mcp_failure_persists_failed_publishes_event_and_reraises() -> None:
    operation = pending_operation()
    operations = OperationsStore(operation)
    banking = RecordingBanking(fail=SystemUnavailable("down"))
    events = RecordingEventPublisher()

    with pytest.raises(SystemUnavailable):
        await use_case(operations, banking, events, FixedClock(NOW)).execute(
            operation_hash=operation.operation_hash, user_id=USER_ID
        )

    assert operations.items[operation.operation_hash].status is OperationStatus.FAILED
    assert [e.event_type for e in events.events] == ["banking.CardLimitChangeFailed"]


async def test_expired_at_execute_persists_expiry_and_never_reaches_mcp() -> None:
    operation = pending_operation(ttl_minutes=5)
    operations = OperationsStore(operation)
    banking, events = RecordingBanking(), RecordingEventPublisher()
    clock = FixedClock(NOW)
    clock.advance(minutes=6)

    receipt = await use_case(operations, banking, events, clock).execute(
        operation_hash=operation.operation_hash, user_id=USER_ID
    )

    assert receipt is None
    assert banking.commands == []
    assert operations.items[operation.operation_hash].status is OperationStatus.EXPIRED
    assert [e.event_type for e in events.events] == ["banking.OperationConfirmationExpired"]


async def test_replay_after_executed_returns_none_without_a_second_mcp_call() -> None:
    operation = pending_operation()
    operations = OperationsStore(operation)
    banking, events = RecordingBanking(), RecordingEventPublisher()
    executor = use_case(operations, banking, events, FixedClock(NOW))

    await executor.execute(operation_hash=operation.operation_hash, user_id=USER_ID)
    replay = await executor.execute(operation_hash=operation.operation_hash, user_id=USER_ID)

    assert replay is None
    assert len(banking.commands) == 1


async def test_executing_marker_recovers_by_replaying_the_idempotency_key() -> None:
    operation = pending_operation().begin_execution(now=NOW)
    operations = OperationsStore(operation)
    banking, events = RecordingBanking(), RecordingEventPublisher()

    receipt = await use_case(operations, banking, events, FixedClock(NOW)).execute(
        operation_hash=operation.operation_hash, user_id=USER_ID
    )

    assert receipt is not None
    assert [c.idempotency_key for c in banking.commands] == [operation.operation_hash]
    assert operations.items[operation.operation_hash].status is OperationStatus.EXECUTED


async def test_cancel_only_applies_to_a_pending_confirmation() -> None:
    operation = pending_operation().begin_execution(now=NOW)
    operations = OperationsStore(operation)
    banking, events = RecordingBanking(), RecordingEventPublisher()

    cancelled = await use_case(operations, banking, events, FixedClock(NOW)).cancel(
        operation_hash=operation.operation_hash, user_id=USER_ID, reason="user_cancelled"
    )

    assert cancelled is False
    assert events.events == []
