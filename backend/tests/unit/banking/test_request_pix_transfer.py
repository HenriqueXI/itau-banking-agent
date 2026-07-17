import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from banking.application.dto import PixTransferConfirmation, PixTransferRejected, RequestPixTransfer
from banking.application.use_cases.request_pix_transfer import RequestPixTransferUseCase
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.values import BankAccount, CustomerProfile


class BankingFake:
    async def get_customer_profile(self, customer_id: str) -> CustomerProfile:
        return CustomerProfile(
            customer_id=customer_id,
            name="Ana Souza",
            segment="Personnalité",
            credit_score=820,
            accounts=(BankAccount(account_id="acc-1", type="checking"),),
        )


class OperationsFake:
    def __init__(self) -> None:
        self.items: list[PendingOperation] = []

    async def add(self, operation: PendingOperation) -> None:
        self.items.append(operation)

    async def get(self, operation_hash: str, *, lock: bool = False) -> PendingOperation | None:
        return next((item for item in self.items if item.operation_hash == operation_hash), None)

    async def get_active_for_user(
        self, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None:
        return next(
            (
                item
                for item in self.items
                if item.user_id == user_id
                and item.status
                in (OperationStatus.PENDING_STEP_UP, OperationStatus.PENDING_CONFIRMATION)
            ),
            None,
        )

    async def save(self, operation: PendingOperation) -> None:
        self.items = [
            operation if item.operation_id == operation.operation_id else item
            for item in self.items
        ]


class TransfersFake:
    def __init__(self, remaining: Decimal | None = None) -> None:
        self.remaining = remaining
        self.reserved = []
        self.released: list[str] = []

    async def reserve(self, transfer, *, daily_limit: Decimal) -> Decimal | None:
        self.reserved.append((transfer, daily_limit))
        return self.remaining

    async def release(self, operation_hash: str) -> None:
        self.released.append(operation_hash)

    async def execute(self, operation_hash: str, receipt) -> None:
        return None

    async def receipt_for(self, operation_hash: str):
        return None


def _use_case(
    transfers: TransfersFake,
    *,
    operations: OperationsFake | None = None,
    clock: FixedClock | None = None,
    ids: SequentialIdGenerator | None = None,
    events: RecordingEventPublisher | None = None,
) -> RequestPixTransferUseCase:
    return RequestPixTransferUseCase(
        banking=BankingFake(),
        operations=operations or OperationsFake(),
        transfers=transfers,
        events=events or RecordingEventPublisher(),
        clock=clock or FixedClock(datetime(2026, 7, 15, 3, tzinfo=UTC)),
        id_generator=ids or SequentialIdGenerator(),
        confirmation_ttl=timedelta(minutes=5),
        daily_limit=Decimal("5000"),
        step_up_threshold=Decimal("1000"),
    )


async def test_pix_above_threshold_requires_step_up_and_reserves_amount() -> None:
    transfers = TransfersFake()
    result = await _use_case(transfers).execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="ana@example.com",
            amount=Decimal("1000.01"),
        )
    )
    assert isinstance(result, PixTransferConfirmation)
    assert result.requires_step_up is True
    assert transfers.reserved[0][0].local_day.isoformat() == "2026-07-15"


async def test_pix_at_threshold_skips_step_up_but_keeps_confirmation() -> None:
    result = await _use_case(TransfersFake()).execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="ana@example.com",
            amount=Decimal("1000.00"),
        )
    )
    assert isinstance(result, PixTransferConfirmation)
    assert result.requires_step_up is False


async def test_pix_under_threshold_999_99_skips_step_up() -> None:
    result = await _use_case(TransfersFake()).execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="ana@example.com",
            amount=Decimal("999.99"),
        )
    )
    assert isinstance(result, PixTransferConfirmation)
    assert result.requires_step_up is False


async def test_invalid_key_is_denied_before_any_reservation() -> None:
    transfers = TransfersFake()
    result = await _use_case(transfers).execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="not-a-key",
            amount=Decimal("100.00"),
        )
    )
    assert isinstance(result, PixTransferRejected)
    assert transfers.reserved == []


async def test_profile_without_accounts_is_denied() -> None:
    class NoAccounts(BankingFake):
        async def get_customer_profile(self, customer_id: str) -> CustomerProfile:
            profile = await super().get_customer_profile(customer_id)
            return CustomerProfile(
                customer_id=profile.customer_id,
                name=profile.name,
                segment=profile.segment,
                credit_score=profile.credit_score,
                accounts=(),
            )

    use_case = RequestPixTransferUseCase(
        banking=NoAccounts(),
        operations=OperationsFake(),
        transfers=TransfersFake(),
        events=RecordingEventPublisher(),
        clock=FixedClock(datetime(2026, 7, 15, 3, tzinfo=UTC)),
        id_generator=SequentialIdGenerator(),
        confirmation_ttl=timedelta(minutes=5),
        daily_limit=Decimal("5000"),
        step_up_threshold=Decimal("1000"),
    )
    result = await use_case.execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="ana@example.com",
            amount=Decimal("100.00"),
        )
    )
    assert isinstance(result, PixTransferRejected)
    assert result.reason == "own_account_missing"


async def test_daily_limit_denial_does_not_create_operation() -> None:
    result = await _use_case(TransfersFake(remaining=Decimal("4999.99"))).execute(
        RequestPixTransfer(
            actor_user_id=uuid.UUID(int=1),
            customer_id="123",
            recipient_key="ana@example.com",
            amount=Decimal("5000.00"),
        )
    )
    assert isinstance(result, PixTransferRejected)
    assert result.reason == "daily_limit_exceeded"
    assert result.remaining_limit == Decimal("4999.99")


async def test_replaying_an_active_identical_pix_reuses_its_confirmation() -> None:
    operations, transfers = OperationsFake(), TransfersFake()
    use_case = _use_case(transfers, operations=operations)
    command = RequestPixTransfer(
        actor_user_id=uuid.UUID(int=1),
        customer_id="123",
        recipient_key="ana@example.com",
        amount=Decimal("100.00"),
    )

    first = await use_case.execute(command)
    second = await use_case.execute(command)

    assert isinstance(first, PixTransferConfirmation)
    assert isinstance(second, PixTransferConfirmation)
    assert second.operation is first.operation
    assert len(operations.items) == 1
    assert len(transfers.reserved) == 1


async def test_expired_identical_pix_creates_a_fresh_confirmation_and_releases_reservation() -> (
    None
):
    operations, transfers, events = OperationsFake(), TransfersFake(), RecordingEventPublisher()
    clock = FixedClock(datetime(2026, 7, 15, 3, tzinfo=UTC))
    use_case = _use_case(
        transfers,
        operations=operations,
        clock=clock,
        ids=SequentialIdGenerator(),
        events=events,
    )
    command = RequestPixTransfer(
        actor_user_id=uuid.UUID(int=1),
        customer_id="123",
        recipient_key="ana@example.com",
        amount=Decimal("100.00"),
    )

    first = await use_case.execute(command)
    clock.advance(minutes=6)
    second = await use_case.execute(command)

    assert isinstance(first, PixTransferConfirmation)
    assert isinstance(second, PixTransferConfirmation)
    assert second.operation.operation_hash != first.operation.operation_hash
    assert first.operation.status is OperationStatus.PENDING_CONFIRMATION
    assert operations.items[0].status is OperationStatus.EXPIRED
    assert second.operation.expires_at > clock.now()
    assert transfers.released == [first.operation.operation_hash]
    assert [event.event_type for event in events.events] == [
        "banking.OperationConfirmationExpired",
    ]
