"""Create a reservation-backed PIX operation (BR-3)."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from banking.application.dto import (
    PixTransferConfirmation,
    PixTransferRejected,
    PixTransferRequestResult,
    RequestPixTransfer,
)
from banking.application.ports.banking_systems import BankingSystemsPort
from banking.application.ports.pending_operation_repository import (
    PendingOperationRepository,
    PixTransferRepository,
)
from banking.domain.events import (
    OperationConfirmationCancelled,
    OperationConfirmationExpired,
    PixTransferDenied,
)
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.pix import PixTransfer, PixValidationError
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class RequestPixTransferUseCase:
    def __init__(
        self,
        *,
        banking: BankingSystemsPort,
        operations: PendingOperationRepository,
        transfers: PixTransferRepository,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
        confirmation_ttl: timedelta,
        daily_limit: Decimal,
        step_up_threshold: Decimal,
    ) -> None:
        self._banking = banking
        self._operations = operations
        self._transfers = transfers
        self._events = events
        self._clock = clock
        self._ids = id_generator
        self._ttl = confirmation_ttl
        self._daily_limit = daily_limit
        self._threshold = step_up_threshold

    async def execute(self, command: RequestPixTransfer) -> PixTransferRequestResult:
        now = self._clock.now()
        profile = await self._banking.get_customer_profile(command.customer_id)
        if not profile.accounts:
            return await self._denied(command, "own_account_missing", None)
        account_id = profile.accounts[0].account_id
        params = {
            "customer_id": command.customer_id,
            "account_id": account_id,
            "recipient_key": command.recipient_key,
            "amount": str(command.amount),
        }
        operation = PendingOperation.create(
            operation_id=self._ids.new_id(),
            user_id=command.actor_user_id,
            tool="fazer_pix",
            params=params,
            tier=3,
            now=now,
            ttl=self._ttl,
        )
        try:
            transfer = PixTransfer.create(
                operation_hash=operation.operation_hash,
                customer_id=command.customer_id,
                account_id=account_id,
                recipient_key=command.recipient_key,
                amount=command.amount,
                local_day=now.astimezone(SAO_PAULO).date(),
            )
        except PixValidationError as error:
            return await self._denied(command, str(error), None)

        active = await self._operations.get_active_for_user(command.actor_user_id, lock=True)
        if active is not None:
            if active.tool == "fazer_pix" and active.params == params and now < active.expires_at:
                return PixTransferConfirmation(
                    operation=active,
                    account_id=account_id,
                    recipient_key_masked=transfer.recipient_key_masked,
                    amount=command.amount,
                    requires_step_up=active.status is OperationStatus.PENDING_STEP_UP,
                )
            if now >= active.expires_at:
                await self._operations.save(active.expire(now=now))
                if active.tool == "fazer_pix":
                    await self._transfers.release(active.operation_hash)
                await self._events.publish(
                    OperationConfirmationExpired(
                        event_id=self._ids.new_id(),
                        occurred_at=now,
                        actor_user_id=str(command.actor_user_id),
                        operation_hash=active.operation_hash,
                        tool=active.tool,
                    )
                )
            else:
                await self._operations.save(active.cancel(reason="params_changed", now=now))
                if active.tool == "fazer_pix":
                    await self._transfers.release(active.operation_hash)
                await self._events.publish(
                    OperationConfirmationCancelled(
                        event_id=self._ids.new_id(),
                        occurred_at=now,
                        actor_user_id=str(command.actor_user_id),
                        operation_hash=active.operation_hash,
                        tool=active.tool,
                        reason="params_changed",
                    )
                )
        remaining: Decimal | None = await self._transfers.reserve(
            transfer, daily_limit=self._daily_limit
        )
        if remaining is not None:
            return await self._denied(command, "daily_limit_exceeded", remaining)
        status = (
            OperationStatus.PENDING_STEP_UP
            if transfer.amount > self._threshold
            else OperationStatus.PENDING_CONFIRMATION
        )
        operation = replace(operation, status=status)
        await self._operations.add(operation)
        return PixTransferConfirmation(
            operation=operation,
            account_id=account_id,
            recipient_key_masked=transfer.recipient_key_masked,
            amount=transfer.amount,
            requires_step_up=status is OperationStatus.PENDING_STEP_UP,
        )

    async def _denied(
        self, command: RequestPixTransfer, reason: str, remaining: Decimal | None
    ) -> PixTransferRejected:
        await self._events.publish(
            PixTransferDenied(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                actor_user_id=str(command.actor_user_id),
                operation_hash=None,
                customer_id=command.customer_id,
                amount=command.amount,
                reason=reason,
                remaining_limit=remaining,
            )
        )
        return PixTransferRejected(reason=reason, remaining_limit=remaining)
