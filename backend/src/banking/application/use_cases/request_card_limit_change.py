"""Create a confirmation-bound card-limit change after fresh eligibility checks."""

from datetime import timedelta

from banking.application.dto import (
    LimitChangeConfirmation,
    LimitChangeRejected,
    LimitChangeRequestResult,
    RequestCardLimitChange,
)
from banking.application.ports.banking_systems import BankingSystemsPort
from banking.application.ports.pending_operation_repository import PendingOperationRepository
from banking.domain.eligibility import EligibilityPolicy
from banking.domain.events import (
    CardLimitChangeDenied,
    CardLimitChangeRequested,
    OperationConfirmationCancelled,
    OperationConfirmationExpired,
    OperationConfirmationRequested,
)
from banking.domain.pending_operation import PendingOperation
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


class RequestCardLimitChangeUseCase:
    """BR-2.6 forces both MCP reads into this use case, never conversation memory."""

    def __init__(
        self,
        *,
        banking: BankingSystemsPort,
        operations: PendingOperationRepository,
        events: EventPublisher,
        eligibility: EligibilityPolicy,
        clock: Clock,
        id_generator: IdGenerator,
        confirmation_ttl: timedelta,
    ) -> None:
        self._banking = banking
        self._operations = operations
        self._events = events
        self._eligibility = eligibility
        self._clock = clock
        self._ids = id_generator
        self._confirmation_ttl = confirmation_ttl

    async def execute(self, command: RequestCardLimitChange) -> LimitChangeRequestResult:
        profile = await self._banking.get_customer_profile(command.customer_id)
        limit = await self._banking.get_card_limit(command.customer_id, command.card_id)
        decision = self._eligibility.evaluate(
            segment=profile.segment,
            credit_score=profile.credit_score,
            current_limit=limit.current_limit,
            used_amount=limit.used_amount,
            requested_limit=command.new_limit,
        )
        if not decision.eligible:
            if decision.reason is None:
                raise RuntimeError("Ineligible card-limit decision must include a reason")
            await self._events.publish(
                CardLimitChangeDenied(
                    event_id=self._ids.new_id(),
                    occurred_at=self._clock.now(),
                    actor_user_id=str(command.actor_user_id),
                    customer_id=command.customer_id,
                    card_id=command.card_id,
                    requested_limit=command.new_limit,
                    maximum=decision.maximum,
                    reason=decision.reason.value,
                )
            )
            return LimitChangeRejected(reason=decision.reason, maximum=decision.maximum)

        now = self._clock.now()
        params = {
            "customer_id": command.customer_id,
            "card_id": command.card_id,
            "current_limit": str(limit.current_limit),
            "new_limit": str(command.new_limit),
            "last4": limit.last4,
        }
        active = await self._operations.get_active_for_user(command.actor_user_id, lock=True)
        if active is not None:
            if (
                active.tool == "alterar_limite"
                and active.params == params
                and now < active.expires_at
            ):
                # Retrying an active request only re-displays its existing
                # confirmation. The operation hash remains bound to this one
                # attempt and no duplicate audit events are emitted.
                return LimitChangeConfirmation(operation=active, current_limit=limit.current_limit)
            if now >= active.expires_at:
                await self._operations.save(active.expire(now=now))
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

        operation = PendingOperation.create(
            operation_id=self._ids.new_id(),
            user_id=command.actor_user_id,
            tool="alterar_limite",
            params=params,
            tier=2,
            now=now,
            ttl=self._confirmation_ttl,
        )
        await self._operations.add(operation)
        await self._events.publish(
            CardLimitChangeRequested(
                event_id=self._ids.new_id(),
                occurred_at=now,
                actor_user_id=str(command.actor_user_id),
                operation_hash=operation.operation_hash,
                customer_id=command.customer_id,
                card_id=command.card_id,
                current_limit=limit.current_limit,
                requested_limit=command.new_limit,
            )
        )
        await self._events.publish(
            OperationConfirmationRequested(
                event_id=self._ids.new_id(),
                occurred_at=now,
                actor_user_id=str(command.actor_user_id),
                operation_hash=operation.operation_hash,
                tool=operation.tool,
                expires_at=operation.expires_at.isoformat(),
            )
        )
        return LimitChangeConfirmation(operation=operation, current_limit=limit.current_limit)
