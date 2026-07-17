"""Verify a step-up code (PRD004-FR-4). First consumer: PIX flow (PRD-008).

The caller owns the transaction: `get_for_update` row-locks the challenge so
concurrent verifies serialize and exactly one submission can win.
"""

from identity_access.application.dto import VerifyStepUpCommand
from identity_access.application.ports.step_up_repository import StepUpChallengeRepository
from identity_access.domain.errors import step_up_challenge_not_found
from identity_access.domain.events import StepUpFailed, StepUpSucceeded
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result


class VerifyStepUp:
    def __init__(
        self,
        *,
        challenges: StepUpChallengeRepository,
        clock: Clock,
        id_generator: IdGenerator,
        event_publisher: EventPublisher,
    ) -> None:
        self._challenges = challenges
        self._clock = clock
        self._ids = id_generator
        self._events = event_publisher

    async def execute(self, command: VerifyStepUpCommand) -> Result[None, DomainError]:
        challenge = await self._challenges.get_for_update(command.challenge_id)
        if challenge is None or challenge.user_id != command.user.id:
            # Same error for missing and foreign challenges — no existence leak.
            return Err(step_up_challenge_not_found())

        now = self._clock.now()
        result = challenge.verify(code=command.code, operation_hash=command.operation_hash, now=now)
        # Attempts/used_at must persist on every outcome (lockout accounting).
        await self._challenges.save(challenge)

        if isinstance(result, Ok):
            await self._events.publish(
                StepUpSucceeded(
                    event_id=self._ids.new_id(),
                    occurred_at=now,
                    actor_user_id=str(challenge.user_id),
                    challenge_id=challenge.id,
                    operation_hash=challenge.operation_hash,
                )
            )
        else:
            await self._events.publish(
                StepUpFailed(
                    event_id=self._ids.new_id(),
                    occurred_at=now,
                    actor_user_id=str(challenge.user_id),
                    challenge_id=challenge.id,
                    operation_hash=challenge.operation_hash,
                    reason=result.error.code,
                )
            )
        return result
