"""Expire overdue confirmation-bound operations and publish their audit events."""

from banking.application.ports.pending_operation_repository import (
    PendingOperationRepository,
    PixTransferRepository,
)
from banking.domain.events import OperationConfirmationExpired
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


class ExpirePendingOperationsUseCase:
    def __init__(
        self,
        *,
        operations: PendingOperationRepository,
        transfers: PixTransferRepository | None = None,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._operations = operations
        self._transfers = transfers
        self._events = events
        self._clock = clock
        self._ids = id_generator

    async def execute(self) -> int:
        now = self._clock.now()
        expired = await self._operations.expire_overdue(now=now)
        for operation in expired:
            if self._transfers is not None and operation.tool == "fazer_pix":
                await self._transfers.release(operation.operation_hash)
            await self._events.publish(
                OperationConfirmationExpired(
                    event_id=self._ids.new_id(),
                    occurred_at=now,
                    actor_user_id=str(operation.user_id),
                    operation_hash=operation.operation_hash,
                    tool=operation.tool,
                )
            )
        return len(expired)
