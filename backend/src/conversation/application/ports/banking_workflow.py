"""Conversation-facing banking workflow contract.

The graph only sees these small, typed views.  Translation to banking use
cases and PostgreSQL repositories is confined to the API composition root.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class CardReference:
    """A server-authoritative, safe-to-display card selector."""

    card_id: str
    last4: str


@dataclass(frozen=True, kw_only=True)
class ProfileView:
    customer_id: str
    name: str
    segment: str
    account_ids: tuple[str, ...] = ()
    card_ids: tuple[str, ...] = ()
    cards: tuple[CardReference, ...] = ()


@dataclass(frozen=True, kw_only=True)
class LimitView:
    card_id: str
    last4: str
    current_limit: Decimal
    used_amount: Decimal = Decimal("0")

    @property
    def available_amount(self) -> Decimal:
        return self.current_limit - self.used_amount


@dataclass(frozen=True, kw_only=True)
class BalanceView:
    account_id: str
    available_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class InvoiceView:
    card_id: str
    last4: str
    amount: Decimal
    due_date: str
    status: str


@dataclass(frozen=True, kw_only=True)
class StatementView:
    account_id: str
    entries: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True, kw_only=True)
class HybridInvoiceGuidanceView:
    invoice: InvoiceView
    statement: StatementView


@dataclass(frozen=True, kw_only=True)
class LimitConfirmationView:
    operation_hash: str
    current_limit: Decimal
    requested_limit: Decimal
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class LimitRejectedView:
    reason: str
    maximum: Decimal


@dataclass(frozen=True, kw_only=True)
class LimitReceiptView:
    old_limit: Decimal
    new_limit: Decimal
    last4: str


@dataclass(frozen=True, kw_only=True)
class LimitAuthorizationDeniedView:
    """A pending limit operation became unauthorized before confirmation.

    This is distinct from a missing operation: the caller can give the same
    safe authorization refusal used for a newly denied request.
    """

    reason: str


@dataclass(frozen=True, kw_only=True)
class PixStepUpView:
    operation_hash: str
    amount: Decimal
    recipient_key_masked: str
    account_id: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class PixConfirmationView:
    operation_hash: str
    amount: Decimal
    recipient_key_masked: str
    account_id: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class PixRejectedView:
    reason: str
    remaining_limit: Decimal | None


@dataclass(frozen=True, kw_only=True)
class PixReceiptView:
    transaction_id: str
    e2e_id: str
    amount: Decimal
    recipient_key_masked: str
    account_id: str


@dataclass(frozen=True, kw_only=True)
class OperationFailedView:
    """A confirmed operation that failed at the banking system. The failure is
    already persisted (FAILED + events) — this view exists so the turn can
    narrate it honestly instead of dying with the exception."""

    tool: str
    reason: str


type LimitRequestView = LimitConfirmationView | LimitRejectedView
type ConfirmationView = (
    LimitConfirmationView
    | LimitReceiptView
    | LimitAuthorizationDeniedView
    | PixConfirmationView
    | PixReceiptView
    | OperationFailedView
    | None
)


class BankingWorkflowPort(Protocol):
    async def get_profile(self, *, customer_id: str) -> ProfileView: ...

    async def get_limit(self, *, customer_id: str, card_id: str | None = None) -> LimitView: ...

    async def get_balance(
        self, *, customer_id: str, account_id: str | None = None
    ) -> BalanceView: ...

    async def get_invoice(self, *, customer_id: str, card_id: str | None = None) -> InvoiceView: ...

    async def get_statement(
        self, *, customer_id: str, account_id: str | None = None
    ) -> StatementView: ...

    async def request_limit_change(
        self, *, user_id: uuid.UUID, customer_id: str, card_id: str | None, amount: Decimal
    ) -> LimitRequestView: ...

    async def request_pix(
        self, *, user_id: uuid.UUID, customer_id: str, recipient_key: str, amount: Decimal
    ) -> PixStepUpView | PixConfirmationView | PixRejectedView: ...

    async def resolve_confirmation(
        self, *, user: object, user_id: uuid.UUID, operation_hash: str, response: str
    ) -> ConfirmationView: ...

    async def resolve_step_up(
        self,
        *,
        user: object,
        user_id: uuid.UUID,
        operation_hash: str,
        challenge_id: uuid.UUID,
        code: str,
    ) -> PixConfirmationView | None: ...
