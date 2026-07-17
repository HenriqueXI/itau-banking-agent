"""Confirm and execute a persisted PIX exactly once (BR-3.6)."""

import uuid
from decimal import Decimal

from banking.application.dto import PixTransferReceipt
from banking.application.ports.banking_systems import BankingSystemsPort
from banking.application.ports.pending_operation_repository import (
    PendingOperationRepository,
    PixTransferRepository,
)
from banking.domain.events import (
    OperationConfirmationCancelled,
    OperationConfirmationExpired,
    PixTransferExecuted,
    PixTransferFailed,
)
from banking.domain.pending_operation import OperationStatus, TransitionError
from banking.domain.values import PixCommand
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


class ExecutePixTransferUseCase:
    def __init__(
        self,
        *,
        operations: PendingOperationRepository,
        transfers: PixTransferRepository,
        banking: BankingSystemsPort,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._operations, self._transfers, self._banking = operations, transfers, banking
        self._events, self._clock, self._ids = events, clock, id_generator

    async def cancel(self, *, operation_hash: str, user_id: uuid.UUID, reason: str) -> bool:
        operation = await self._operations.get_for_user(operation_hash, user_id, lock=True)
        if operation is None or operation.status not in (
            OperationStatus.PENDING_STEP_UP,
            OperationStatus.PENDING_CONFIRMATION,
        ):
            return False
        operation = operation.cancel(reason=reason, now=self._clock.now())
        await self._operations.save(operation)
        await self._transfers.release(operation_hash)
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
    ) -> PixTransferReceipt | None:
        operation = await self._operations.get_for_user(operation_hash, user_id, lock=True)
        if operation is None:
            return None
        if operation.status is OperationStatus.EXECUTED:
            receipt = await self._transfers.receipt_for(operation_hash)
            return (
                PixTransferReceipt(receipt=receipt, account_id=str(operation.params["account_id"]))
                if receipt
                else None
            )
        try:
            executing = operation.begin_execution(now=self._clock.now())
        except TransitionError:
            if operation.status is OperationStatus.EXECUTING:
                # Crash recovery (workflows.md §4): the marker was committed but
                # the outcome wasn't. Replay the MCP call with the stored
                # idempotency key — the server dedupes, so money moves once.
                executing = operation
            else:
                if (
                    operation.status
                    in (OperationStatus.PENDING_STEP_UP, OperationStatus.PENDING_CONFIRMATION)
                    and self._clock.now() >= operation.expires_at
                ):
                    await self._operations.save(operation.expire(now=self._clock.now()))
                    await self._transfers.release(operation_hash)
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
            receipt = await self._banking.execute_pix(
                PixCommand(
                    from_customer_id=str(params["customer_id"]),
                    from_account_id=str(params["account_id"]),
                    recipient_key=str(params["recipient_key"]),
                    amount=Decimal(str(params["amount"])),
                    idempotency_key=executing.idempotency_key or operation_hash,
                )
            )
        except Exception as error:
            await self._operations.save(executing.fail(now=self._clock.now()))
            await self._transfers.release(operation_hash)
            await self._events.publish(
                PixTransferFailed(
                    event_id=self._ids.new_id(),
                    occurred_at=self._clock.now(),
                    actor_user_id=str(user_id),
                    operation_hash=operation_hash,
                    customer_id=str(params["customer_id"]),
                    amount=Decimal(str(params["amount"])),
                    reason=type(error).__name__,
                )
            )
            raise
        await self._transfers.execute(operation_hash, receipt)
        await self._operations.save(executing.complete(now=self._clock.now()))
        await self._events.publish(
            PixTransferExecuted(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                actor_user_id=str(user_id),
                operation_hash=operation_hash,
                customer_id=str(params["customer_id"]),
                account_id=str(params["account_id"]),
                amount=receipt.amount,
                recipient_key_masked=receipt.recipient_key_masked,
                idempotency_key=executing.idempotency_key or operation_hash,
                transaction_id=receipt.transaction_id,
                e2e_id=receipt.e2e_id,
            )
        )
        return PixTransferReceipt(receipt=receipt, account_id=str(params["account_id"]))
