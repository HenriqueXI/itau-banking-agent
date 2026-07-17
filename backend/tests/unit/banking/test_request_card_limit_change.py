import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from banking.application.dto import (
    LimitChangeConfirmation,
    LimitChangeRejected,
    RequestCardLimitChange,
)
from banking.application.use_cases.request_card_limit_change import RequestCardLimitChangeUseCase
from banking.domain.eligibility import EligibilityPolicy, LimitChangeDenial
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.values import CardLimit, CustomerProfile


class BankingFake:
    def __init__(self, *, limit: Decimal = Decimal("5000")) -> None:
        self.calls: list[str] = []
        self.limit = limit

    async def get_customer_profile(self, customer_id: str) -> CustomerProfile:
        self.calls.append("profile")
        return CustomerProfile(
            customer_id=customer_id,
            name="Ana Souza",
            segment="Personnalité",
            credit_score=820,
            accounts=(),
        )

    async def get_card_limit(self, customer_id: str, card_id: str) -> CardLimit:
        self.calls.append("limit")
        return CardLimit(
            card_id=card_id,
            customer_id=customer_id,
            current_limit=self.limit,
            currency="BRL",
            last4="4242",
        )


class OperationsFake:
    def __init__(self, *, existing: object | None = None, active: object | None = None) -> None:
        self.items: list[object] = []
        self.saved: list[object] = []
        self._existing = existing
        self._active = active

    async def add(self, operation: object) -> None:
        self.items.append(operation)

    async def get(self, operation_hash: str, *, lock: bool = False) -> object | None:
        return self._existing

    async def get_active_for_user(self, user_id: uuid.UUID, *, lock: bool = False) -> object | None:
        return self._active

    async def save(self, operation: object) -> None:
        self.saved.append(operation)


def _use_case(
    fake: BankingFake,
    operations: OperationsFake | None = None,
    events: RecordingEventPublisher | None = None,
) -> RequestCardLimitChangeUseCase:
    return RequestCardLimitChangeUseCase(
        banking=fake,
        operations=operations or OperationsFake(),
        events=events or RecordingEventPublisher(),
        eligibility=EligibilityPolicy(),
        clock=FixedClock(datetime(2026, 7, 15, tzinfo=UTC)),
        id_generator=SequentialIdGenerator(),
        confirmation_ttl=timedelta(minutes=5),
    )


async def test_request_fetches_fresh_profile_and_limit_then_creates_a_bound_confirmation() -> None:
    fake = BankingFake()
    operations = OperationsFake()
    events = RecordingEventPublisher()
    result = await _use_case(fake, operations, events).execute(
        RequestCardLimitChange(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            card_id="card-1",
            new_limit=Decimal("15000"),
        )
    )

    assert fake.calls == ["profile", "limit"]
    assert isinstance(result, LimitChangeConfirmation)
    assert result.current_limit == Decimal("5000")
    assert result.operation.params["new_limit"] == "15000"
    assert result.operation.expires_at == datetime(2026, 7, 15, 0, 5, tzinfo=UTC)
    assert operations.items == [result.operation]
    assert [event.event_type for event in events.events] == [
        "banking.CardLimitChangeRequested",
        "banking.OperationConfirmationRequested",
    ]


async def test_new_params_cancel_the_active_pending_op_as_params_changed() -> None:
    active = PendingOperation.create(
        operation_id=uuid.UUID(int=7),
        user_id=uuid.UUID(int=1),
        tool="alterar_limite",
        params={"customer_id": "123", "card_id": "card-1", "new_limit": Decimal("15000")},
        tier=2,
        now=datetime(2026, 7, 15, tzinfo=UTC),
        ttl=timedelta(minutes=5),
    )
    operations = OperationsFake(active=active)
    events = RecordingEventPublisher()

    result = await _use_case(BankingFake(), operations, events).execute(
        RequestCardLimitChange(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            card_id="card-1",
            new_limit=Decimal("12000"),
        )
    )

    assert isinstance(result, LimitChangeConfirmation)
    assert result.operation.params["new_limit"] == "12000"
    cancelled = operations.saved[0]
    assert cancelled.status is OperationStatus.CANCELLED
    assert cancelled.cancellation_reason == "params_changed"
    assert [event.event_type for event in events.events] == [
        "banking.OperationConfirmationCancelled",
        "banking.CardLimitChangeRequested",
        "banking.OperationConfirmationRequested",
    ]


async def test_replaying_the_same_request_reuses_the_confirmation_without_new_events() -> None:
    user_id = uuid.UUID(int=1)
    now = datetime(2026, 7, 15, tzinfo=UTC)
    existing = PendingOperation.create(
        operation_id=uuid.UUID(int=7),
        user_id=user_id,
        tool="alterar_limite",
        params={
            "customer_id": "123",
            "card_id": "card-1",
            "current_limit": "5000",
            "new_limit": "15000",
            "last4": "4242",
        },
        tier=2,
        now=now,
        ttl=timedelta(minutes=5),
    )
    operations = OperationsFake(active=existing)
    events = RecordingEventPublisher()

    result = await _use_case(BankingFake(), operations, events).execute(
        RequestCardLimitChange(
            actor_user_id=user_id,
            customer_id="123",
            card_id="card-1",
            new_limit=Decimal("15000"),
        )
    )

    assert isinstance(result, LimitChangeConfirmation)
    assert result.operation is existing
    assert operations.items == []
    assert events.events == []


async def test_ineligible_request_returns_reason_and_never_creates_confirmation() -> None:
    result = await _use_case(BankingFake()).execute(
        RequestCardLimitChange(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            card_id="card-1",
            new_limit=Decimal("50100"),
        )
    )

    assert isinstance(result, LimitChangeRejected)
    assert result.reason is LimitChangeDenial.ABOVE_MAXIMUM
    assert result.maximum == Decimal("50000")
