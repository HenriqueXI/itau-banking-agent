"""Auditable facts emitted by the card-limit workflow."""

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class CardLimitChangeRequested(DomainEvent):
    event_type: ClassVar[str] = "banking.CardLimitChangeRequested"

    operation_hash: str
    customer_id: str
    card_id: str
    current_limit: Decimal
    requested_limit: Decimal


@dataclass(frozen=True, kw_only=True)
class CardLimitChangeDenied(DomainEvent):
    event_type: ClassVar[str] = "banking.CardLimitChangeDenied"

    customer_id: str
    card_id: str
    requested_limit: Decimal
    maximum: Decimal
    reason: str


@dataclass(frozen=True, kw_only=True)
class CardLimitChanged(DomainEvent):
    event_type: ClassVar[str] = "banking.CardLimitChanged"

    operation_hash: str
    customer_id: str
    card_id: str
    old_limit: Decimal
    new_limit: Decimal


@dataclass(frozen=True, kw_only=True)
class CardLimitChangeFailed(DomainEvent):
    event_type: ClassVar[str] = "banking.CardLimitChangeFailed"

    operation_hash: str
    customer_id: str
    card_id: str
    requested_limit: Decimal
    reason: str


@dataclass(frozen=True, kw_only=True)
class OperationConfirmationRequested(DomainEvent):
    event_type: ClassVar[str] = "banking.OperationConfirmationRequested"

    operation_hash: str
    tool: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class OperationConfirmationCancelled(DomainEvent):
    event_type: ClassVar[str] = "banking.OperationConfirmationCancelled"

    operation_hash: str
    tool: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class OperationConfirmationExpired(DomainEvent):
    event_type: ClassVar[str] = "banking.OperationConfirmationExpired"

    operation_hash: str
    tool: str


@dataclass(frozen=True, kw_only=True)
class PixTransferDenied(DomainEvent):
    event_type: ClassVar[str] = "banking.PixTransferDenied"

    operation_hash: str | None
    customer_id: str
    amount: Decimal
    reason: str
    remaining_limit: Decimal | None = None


@dataclass(frozen=True, kw_only=True)
class PixTransferExecuted(DomainEvent):
    event_type: ClassVar[str] = "banking.PixTransferExecuted"

    operation_hash: str
    customer_id: str
    account_id: str
    amount: Decimal
    recipient_key_masked: str
    idempotency_key: str
    transaction_id: str
    e2e_id: str


@dataclass(frozen=True, kw_only=True)
class PixTransferFailed(DomainEvent):
    event_type: ClassVar[str] = "banking.PixTransferFailed"

    operation_hash: str
    customer_id: str
    amount: Decimal
    reason: str
