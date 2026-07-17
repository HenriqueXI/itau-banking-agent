"""Framework-free values crossing the ``BankingSystemsPort``."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class BankAccount:
    account_id: str
    type: str
    available_balance: Decimal | None = None


@dataclass(frozen=True, kw_only=True)
class BankCard:
    card_id: str
    last4: str


@dataclass(frozen=True, kw_only=True)
class CustomerProfile:
    customer_id: str
    name: str
    segment: str
    credit_score: int
    accounts: tuple[BankAccount, ...]
    cards: tuple[BankCard, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CardLimit:
    card_id: str
    customer_id: str
    current_limit: Decimal
    currency: str
    last4: str
    used_amount: Decimal = Decimal("0")

    @property
    def available_amount(self) -> Decimal:
        return self.current_limit - self.used_amount


@dataclass(frozen=True, kw_only=True)
class CardInvoice:
    card_id: str
    customer_id: str
    last4: str
    amount: Decimal
    due_date: str
    status: str
    currency: str
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class StatementEntry:
    transaction_id: str
    description: str
    amount: Decimal
    occurred_at: datetime
    kind: str


@dataclass(frozen=True, kw_only=True)
class AccountStatement:
    account_id: str
    customer_id: str
    entries: tuple[StatementEntry, ...]


@dataclass(frozen=True, kw_only=True)
class LimitUpdateCommand:
    customer_id: str
    card_id: str
    new_limit: Decimal
    requested_by: str
    idempotency_key: str


@dataclass(frozen=True, kw_only=True)
class LimitUpdateReceipt:
    card_id: str
    old_limit: Decimal
    new_limit: Decimal
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class PixCommand:
    from_customer_id: str
    from_account_id: str
    recipient_key: str
    amount: Decimal
    idempotency_key: str


@dataclass(frozen=True, kw_only=True)
class PixReceipt:
    transaction_id: str
    status: str
    amount: Decimal
    recipient_key_masked: str
    executed_at: datetime
    e2e_id: str
