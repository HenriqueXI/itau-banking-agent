"""VerifyStepUp use case: events, ownership, persistence of attempts."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.identity import InMemoryStepUpRepository
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from identity_access.application.dto import VerifyStepUpCommand
from identity_access.application.use_cases.verify_step_up import VerifyStepUp
from identity_access.domain.entities import StepUpChallenge
from identity_access.domain.events import StepUpFailed, StepUpSucceeded
from identity_access.domain.values import AuthenticatedUser, Role
from shared.domain.result import is_err, is_ok

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # FixedClock default
ANA = AuthenticatedUser(id=uuid.UUID(int=10), role=Role.CUSTOMER, customer_id="123")
OTHER = AuthenticatedUser(id=uuid.UUID(int=99), role=Role.MANAGER)


@pytest.fixture
def repo() -> InMemoryStepUpRepository:
    return InMemoryStepUpRepository()


@pytest.fixture
def events() -> RecordingEventPublisher:
    return RecordingEventPublisher()


@pytest.fixture
def use_case(repo: InMemoryStepUpRepository, events: RecordingEventPublisher) -> VerifyStepUp:
    return VerifyStepUp(
        challenges=repo,
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        event_publisher=events,
    )


async def seed_challenge(repo: InMemoryStepUpRepository) -> StepUpChallenge:
    challenge = StepUpChallenge.issue(
        challenge_id=uuid.UUID(int=1),
        user_id=ANA.id,
        operation_hash="op-1",
        code="123456",
        now=NOW,
        ttl=timedelta(minutes=5),
    )
    await repo.add(challenge)
    return challenge


async def test_correct_code_succeeds_and_raises_event(
    use_case: VerifyStepUp, repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    challenge = await seed_challenge(repo)
    result = await use_case.execute(
        VerifyStepUpCommand(
            user=ANA, challenge_id=challenge.id, operation_hash="op-1", code="123456"
        )
    )
    assert is_ok(result)
    (event,) = events.events
    assert isinstance(event, StepUpSucceeded)
    assert repo.saves == 1


async def test_failure_raises_failed_event_with_reason(
    use_case: VerifyStepUp, repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    challenge = await seed_challenge(repo)
    result = await use_case.execute(
        VerifyStepUpCommand(
            user=ANA, challenge_id=challenge.id, operation_hash="op-1", code="000000"
        )
    )
    assert is_err(result)
    (event,) = events.events
    assert isinstance(event, StepUpFailed)
    assert event.reason == "step_up.invalid_code"
    # Attempt bookkeeping persisted even on failure (lockout accounting).
    assert repo.saves == 1
    assert repo.challenges[challenge.id].attempts == 1


async def test_foreign_challenge_looks_nonexistent(
    use_case: VerifyStepUp, repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    challenge = await seed_challenge(repo)
    result = await use_case.execute(
        VerifyStepUpCommand(
            user=OTHER, challenge_id=challenge.id, operation_hash="op-1", code="123456"
        )
    )
    assert is_err(result)
    assert result.error.code == "step_up.challenge_not_found"
    assert not events.events


async def test_unknown_challenge_id(use_case: VerifyStepUp) -> None:
    result = await use_case.execute(
        VerifyStepUpCommand(
            user=ANA, challenge_id=uuid.UUID(int=77), operation_hash="op-1", code="123456"
        )
    )
    assert is_err(result)
    assert result.error.code == "step_up.challenge_not_found"
