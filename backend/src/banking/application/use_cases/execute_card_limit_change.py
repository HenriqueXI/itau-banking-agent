"""Confirm and execute a persisted limit change exactly once per operation hash."""

import uuid
from decimal import Decimal

from banking.application.ports.banking_systems import BankingSystemsPort
from banking.application.ports.pending_operation_repository import PendingOperationRepository
from banking.domain.events import (
    CardLimitChanged,
    CardLimitChangeFailed,
    OperationConfirmationCancelled,
    OperationConfirmationExpired,
)
from banking.domain.pending_operation import OperationStatus, TransitionError
from banking.domain.values import LimitUpdateCommand, LimitUpdateReceipt
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


class ExecuteCardLimitChangeUseCase:
    def __init__(
        self,
        *,
        operations: PendingOperationRepository,
        banking: BankingSystemsPort,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._operations = operations
        self._banking = banking
        self._events = events
        self._clock = clock
        self._ids = id_generator

    async def cancel(self, *, operation_hash: str, user_id: uuid.UUID, reason: str) -> bool:
        operation = await self._operations.get_for_user(operation_hash, user_id, lock=True)
        if operation is None or operation.status is not OperationStatus.PENDING_CONFIRMATION:
            return False
        operation = operation.cancel(reason=reason, now=self._clock.now())
        await self._operations.save(operation)
        await self._events.publish(
            OperationConfirmationCancelled(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                actor_user_id=str(user_id),
                operation_hash=operation_hash,
                tool=operation.tool,
                reason=reason,
            )
        )
        return True

    async def execute(
        self, *, operation_hash: str, user_id: uuid.UUID
    ) -> LimitUpdateReceipt | None:
        operation = await self._operations.get_for_user(operation_hash, user_id, lock=True)
        if operation is None:
            return None
        try:
            executing = operation.begin_execution(now=self._clock.now())
        except TransitionError:
            if operation.status is OperationStatus.EXECUTING:
                # Crash recovery (workflows.md §4): a persisted EXECUTING marker
                # means the MCP call may or may not have landed. Replay it with
                # the stored idempotency key — the server dedupes — instead of
                # leaving the operation stuck.
                executing = operation
            else:
                if operation.status is OperationStatus.PENDING_CONFIRMATION:
                    expired = operation.expire(now=self._clock.now())
                    await self._operations.save(expired)
                    await self._events.publish(
                        OperationConfirmationExpired(
                            event_id=self._ids.new_id(),
                            occurred_at=self._clock.now(),
                            actor_user_id=str(user_id),
                            operation_hash=operation_hash,
                            tool=operation.tool,
                        )
                    )
                return None
        await self._operations.save(executing)
        params = executing.params
        try:
            receipt = await self._banking.update_card_limit(
                LimitUpdateCommand(
                    customer_id=str(params["customer_id"]),
                    card_id=str(params["card_id"]),
                    new_limit=Decimal(str(params["new_limit"])),
                    requested_by=f"user:{user_id}",
                    idempotency_key=executing.idempotency_key or operation_hash,
                )
            )
        except Exception as error:
            await self._operations.save(executing.fail(now=self._clock.now()))
            await self._events.publish(
                CardLimitChangeFailed(
                    event_id=self._ids.new_id(),
                    occurred_at=self._clock.now(),
                    actor_user_id=str(user_id),
                    operation_hash=operation_hash,
                    customer_id=str(params["customer_id"]),
                    card_id=str(params["card_id"]),
                    requested_limit=Decimal(str(params["new_limit"])),
                    reason=type(error).__name__,
                )
            )
            raise
        await self._operations.save(executing.complete(now=self._clock.now()))
        await self._events.publish(
            CardLimitChanged(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                actor_user_id=str(user_id),
                operation_hash=operation_hash,
                customer_id=str(params["customer_id"]),
                card_id=str(params["card_id"]),
                old_limit=receipt.old_limit,
                new_limit=receipt.new_limit,
            )
        )
        return receipt
