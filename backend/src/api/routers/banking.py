"""Read-only financial summary for the authenticated user's panel.

The route deliberately composes MCP reads instead of importing a banking
repository.  The browser gets an authoritative snapshot, while the agent keeps
using the exact same BankingSystemsPort through its workflow adapter.
"""

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.dependencies import CurrentUser
from api.problem import ProblemError

router = APIRouter(prefix="/api/banking")


class CardSummary(BaseModel):
    card_id: str
    last4: str
    total_limit: str
    used_amount: str
    available_amount: str
    invoice_amount: str
    due_date: str
    invoice_status: str


class TransactionSummary(BaseModel):
    transaction_id: str
    description: str
    amount: str
    occurred_at: str
    kind: str


class FinancialSummary(BaseModel):
    account_id: str
    available_balance: str
    cards: list[CardSummary]
    transactions: list[TransactionSummary]


def _money(value: Decimal) -> str:
    return format(value, ".2f")


@router.get("/summary", response_model=FinancialSummary)
async def summary(request: Request, user: CurrentUser) -> FinancialSummary:
    customer_id = user.customer_id
    if customer_id is None:
        raise ProblemError(status=404, title="Not Found", detail="Conta bancária não vinculada")
    banking = request.app.state.banking.client
    profile = await banking.get_customer_profile(customer_id)
    if not profile.accounts:
        raise ProblemError(status=404, title="Not Found", detail="Conta bancária não vinculada")
    account_id = profile.accounts[0].account_id
    balance = await banking.get_account_balance(customer_id, account_id)
    cards: list[CardSummary] = []
    for card in profile.cards:
        limit = await banking.get_card_limit(customer_id, card.card_id)
        invoice = await banking.get_card_invoice(customer_id, card.card_id)
        cards.append(
            CardSummary(
                card_id=card.card_id,
                last4=limit.last4,
                total_limit=_money(limit.current_limit),
                used_amount=_money(limit.used_amount),
                available_amount=_money(limit.available_amount),
                invoice_amount=_money(invoice.amount),
                due_date=invoice.due_date,
                invoice_status=invoice.status,
            )
        )
    statement = await banking.get_account_statement(customer_id, account_id)
    return FinancialSummary(
        account_id=account_id,
        available_balance=_money(balance),
        cards=cards,
        transactions=[
            TransactionSummary(
                transaction_id=e.transaction_id,
                description=e.description,
                amount=_money(e.amount),
                occurred_at=e.occurred_at.isoformat(),
                kind=e.kind,
            )
            for e in statement.entries
        ],
    )
