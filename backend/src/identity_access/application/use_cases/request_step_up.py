"""Issue a step-up challenge bound to one operation (PRD004-FR-4/5, BR-5)."""

from datetime import timedelta

from identity_access.application.dto import RequestStepUpCommand, StepUpChallengeIssued
from identity_access.application.ports.code_generator import StepUpCodeGenerator
from identity_access.application.ports.step_up_repository import StepUpChallengeRepository
from identity_access.domain.entities import StepUpChallenge
from identity_access.domain.errors import step_up_missing_operation
from identity_access.domain.events import StepUpIssued
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result


class RequestStepUp:
    def __init__(
        self,
        *,
        challenges: StepUpChallengeRepository,
        code_generator: StepUpCodeGenerator,
        clock: Clock,
        id_generator: IdGenerator,
        event_publisher: EventPublisher,
        ttl_minutes: int,
        reveal_code: bool,
    ) -> None:
        self._challenges = challenges
        self._codes = code_generator
        self._clock = clock
        self._ids = id_generator
        self._events = event_publisher
        self._ttl = timedelta(minutes=ttl_minutes)
        # Simulated delivery (demo only): the code is returned in the response.
        self._reveal_code = reveal_code

    async def execute(
        self, command: RequestStepUpCommand
    ) -> Result[StepUpChallengeIssued, DomainError]:
        if not command.operation_hash:
            return Err(step_up_missing_operation())

        code = self._codes.generate()
        now = self._clock.now()
        challenge = StepUpChallenge.issue(
            challenge_id=self._ids.new_id(),
            user_id=command.user.id,
            operation_hash=command.operation_hash,
            code=code,
            now=now,
            ttl=self._ttl,
        )
        await self._challenges.add(challenge)
        await self._events.publish(
            StepUpIssued(
                event_id=self._ids.new_id(),
                occurred_at=now,
                actor_user_id=str(command.user.id),
                challenge_id=challenge.id,
                operation_hash=challenge.operation_hash,
                expires_at=challenge.expires_at,
            )
        )
        return Ok(
            StepUpChallengeIssued(
                challenge_id=challenge.id,
                expires_at=challenge.expires_at,
                dev_code=code if self._reveal_code else None,
            )
        )
