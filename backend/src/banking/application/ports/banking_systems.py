"""The only application-facing contract for core banking."""

from decimal import Decimal
from typing import Protocol

from banking.domain.values import (
    AccountStatement,
    CardInvoice,
    CardLimit,
    CustomerProfile,
    LimitUpdateCommand,
    LimitUpdateReceipt,
    PixCommand,
    PixReceipt,
)


class BankingSystemsPort(Protocol):
    async def get_customer_profile(self, customer_id: str) -> CustomerProfile: ...

    async def get_card_limit(self, customer_id: str, card_id: str) -> CardLimit: ...

    async def get_account_balance(self, customer_id: str, account_id: str) -> Decimal: ...

    async def get_card_invoice(self, customer_id: str, card_id: str) -> CardInvoice: ...

    async def get_account_statement(
        self, customer_id: str, account_id: str, period: str | None = None
    ) -> AccountStatement: ...

    async def update_card_limit(self, command: LimitUpdateCommand) -> LimitUpdateReceipt: ...

    async def execute_pix(self, command: PixCommand) -> PixReceipt: ...
