"""RequestStepUp use case: challenge persisted, event raised, demo code flag."""

import uuid

import pytest
from tests.fakes.identity import FixedCodeGenerator, InMemoryStepUpRepository
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from identity_access.application.dto import RequestStepUpCommand
from identity_access.application.use_cases.request_step_up import RequestStepUp
from identity_access.domain.events import StepUpIssued
from identity_access.domain.values import AuthenticatedUser, Role
from shared.domain.result import is_err, is_ok

ANA = AuthenticatedUser(id=uuid.UUID(int=10), role=Role.CUSTOMER, customer_id="123")


@pytest.fixture
def repo() -> InMemoryStepUpRepository:
    return InMemoryStepUpRepository()


@pytest.fixture
def events() -> RecordingEventPublisher:
    return RecordingEventPublisher()


def make_use_case(
    repo: InMemoryStepUpRepository,
    events: RecordingEventPublisher,
    reveal_code: bool = True,
) -> RequestStepUp:
    return RequestStepUp(
        challenges=repo,
        code_generator=FixedCodeGenerator("654321"),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        event_publisher=events,
        ttl_minutes=5,
        reveal_code=reveal_code,
    )


async def test_issues_bound_challenge_and_raises_event(
    repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    result = await make_use_case(repo, events).execute(
        RequestStepUpCommand(user=ANA, operation_hash="op-1")
    )
    assert is_ok(result)

    challenge = repo.challenges[result.value.challenge_id]
    assert challenge.user_id == ANA.id
    assert challenge.operation_hash == "op-1"
    assert result.value.expires_at == challenge.expires_at

    (event,) = events.events
    assert isinstance(event, StepUpIssued)
    assert event.operation_hash == "op-1"


async def test_dev_code_present_only_when_revealed(
    repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    revealed = await make_use_case(repo, events, reveal_code=True).execute(
        RequestStepUpCommand(user=ANA, operation_hash="op-1")
    )
    hidden = await make_use_case(repo, events, reveal_code=False).execute(
        RequestStepUpCommand(user=ANA, operation_hash="op-2")
    )
    assert is_ok(revealed) and revealed.value.dev_code == "654321"
    assert is_ok(hidden) and hidden.value.dev_code is None


async def test_missing_operation_hash_creates_nothing(
    repo: InMemoryStepUpRepository, events: RecordingEventPublisher
) -> None:
    result = await make_use_case(repo, events).execute(
        RequestStepUpCommand(user=ANA, operation_hash="")
    )
    assert is_err(result)
    assert result.error.code == "step_up.missing_operation"
    assert not repo.challenges
    assert not events.events
