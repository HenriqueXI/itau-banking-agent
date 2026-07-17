"""Commands and typed outcomes for card-limit workflows."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from banking.domain.eligibility import LimitChangeDenial
from banking.domain.pending_operation import PendingOperation
from banking.domain.values import PixReceipt


@dataclass(frozen=True, kw_only=True)
class RequestCardLimitChange:
    actor_user_id: uuid.UUID
    customer_id: str
    card_id: str
    new_limit: Decimal


@dataclass(frozen=True, kw_only=True)
class LimitChangeRejected:
    reason: LimitChangeDenial
    maximum: Decimal


@dataclass(frozen=True, kw_only=True)
class LimitChangeConfirmation:
    operation: PendingOperation
    current_limit: Decimal


type LimitChangeRequestResult = LimitChangeRejected | LimitChangeConfirmation


@dataclass(frozen=True, kw_only=True)
class RequestPixTransfer:
    actor_user_id: uuid.UUID
    customer_id: str
    recipient_key: str
    amount: Decimal


@dataclass(frozen=True, kw_only=True)
class PixTransferConfirmation:
    operation: PendingOperation
    account_id: str
    recipient_key_masked: str
    amount: Decimal
    requires_step_up: bool


@dataclass(frozen=True, kw_only=True)
class PixTransferRejected:
    reason: str
    remaining_limit: Decimal | None = None


type PixTransferRequestResult = PixTransferConfirmation | PixTransferRejected


@dataclass(frozen=True, kw_only=True)
class PixTransferReceipt:
    receipt: PixReceipt
    account_id: str
