"""AuthorizeAction seam: deny emits AuthorizationDenied; permit stays silent."""

import uuid

import pytest
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from identity_access.application.dto import AuthorizationRequest
from identity_access.application.use_cases.authorize_action import AuthorizeAction
from identity_access.domain.authorization import (
    Action,
    AuthorizationService,
    Deny,
    DenyReason,
    Permit,
)
from identity_access.domain.events import AuthorizationDenied
from identity_access.domain.values import AuthenticatedUser, Role

CUSTOMER = AuthenticatedUser(id=uuid.UUID(int=1), role=Role.CUSTOMER, customer_id="cust-1")


class Target:
    def __init__(self, owner_id: str | None) -> None:
        self.owner_id = owner_id


@pytest.fixture
def events() -> RecordingEventPublisher:
    return RecordingEventPublisher()


def make_use_case(events: RecordingEventPublisher) -> AuthorizeAction:
    return AuthorizeAction(
        service=AuthorizationService(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        event_publisher=events,
    )


async def test_permit_raises_no_event(events: RecordingEventPublisher) -> None:
    decision = await make_use_case(events).execute(
        AuthorizationRequest(user=CUSTOMER, action=Action.VIEW_BALANCE, resource=Target("cust-1"))
    )
    assert isinstance(decision, Permit)
    assert not events.events


async def test_deny_emits_authorization_denied_with_payload(
    events: RecordingEventPublisher,
) -> None:
    decision = await make_use_case(events).execute(
        AuthorizationRequest(user=CUSTOMER, action=Action.VIEW_BALANCE, resource=Target("cust-2"))
    )
    assert decision == Deny(DenyReason.ROLE_FORBIDDEN)

    (event,) = events.events
    assert isinstance(event, AuthorizationDenied)
    assert event.actor_user_id == str(CUSTOMER.id)
    assert event.attempted_action == "view_balance"
    assert event.target_resource == "cust-2"
    assert event.reason == "role_forbidden"


async def test_deny_without_resource_carries_null_target(
    events: RecordingEventPublisher,
) -> None:
    decision = await make_use_case(events).execute(
        AuthorizationRequest(user=CUSTOMER, action=Action.VIEW_AUDIT)
    )
    assert isinstance(decision, Deny)
    (event,) = events.events
    assert isinstance(event, AuthorizationDenied)
    assert event.target_resource is None
