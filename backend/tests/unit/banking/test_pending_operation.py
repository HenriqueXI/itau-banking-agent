import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from banking.domain.pending_operation import OperationStatus, PendingOperation, TransitionError

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _operation(amount: Decimal = Decimal("15000")) -> PendingOperation:
    return PendingOperation.create(
        operation_id=uuid.UUID(int=1),
        user_id=uuid.UUID(int=2),
        tool="alterar_limite",
        params={"customer_id": "123", "card_id": "card-1", "amount": amount},
        tier=2,
        now=NOW,
        ttl=timedelta(minutes=5),
    )


def test_hash_is_unique_for_each_confirmation_attempt() -> None:
    first = _operation()
    second = PendingOperation.create(
        operation_id=uuid.UUID(int=3),
        user_id=uuid.UUID(int=2),
        tool="alterar_limite",
        params={"amount": Decimal("15000"), "card_id": "card-1", "customer_id": "123"},
        tier=2,
        now=NOW,
        ttl=timedelta(minutes=5),
    )

    assert first.operation_hash != second.operation_hash
    assert first.idempotency_key == first.operation_hash
    assert second.idempotency_key == second.operation_hash


def test_confirmation_only_transitions_a_live_pending_operation_to_executing() -> None:
    operation = _operation()

    executing = operation.begin_execution(now=NOW + timedelta(minutes=1))

    assert executing.status is OperationStatus.EXECUTING
    assert executing.idempotency_key == operation.operation_hash


def test_expired_operation_cannot_execute() -> None:
    operation = _operation()

    with pytest.raises(TransitionError, match="expired"):
        operation.begin_execution(now=NOW + timedelta(minutes=6))


def test_terminal_operation_cannot_transition_twice() -> None:
    operation = _operation().cancel(reason="user_cancelled")

    with pytest.raises(TransitionError):
        operation.cancel(reason="again")


def test_terminal_transition_records_resolution_time() -> None:
    resolved = _operation().cancel(reason="user_cancelled", now=NOW)

    assert resolved.resolved_at == NOW


def test_step_up_operation_transitions_to_confirmation() -> None:
    operation = replace(_operation(), status=OperationStatus.PENDING_STEP_UP)

    assert operation.complete_step_up(now=NOW).status is OperationStatus.PENDING_CONFIRMATION
