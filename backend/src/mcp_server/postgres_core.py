"""PostgreSQL implementation of the core-banking MCP contracts.

This module is deliberately owned by the MCP process.  The HTTP API and the
agent use its public tools only through ``BankingSystemsPort``; they never
import this adapter or query its tables.
"""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from shared.demo_personas import DEMO_PERSONAS

EMAIL_PIX_KEY = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PIX_KEY = re.compile(r"^\+[1-9]\d{7,14}$")


class PostgresCoreBanking:
    """Transactional, idempotent simulated banking core backed by PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def get_customer_profile(self, customer_id: str) -> dict[str, Any]:
        persona = _persona(customer_id)
        if persona is None:
            return _error("CUSTOMER_NOT_FOUND")
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT account_id, account_type FROM app.core_accounts "
                "WHERE customer_id = %s ORDER BY account_id",
                (customer_id,),
            )
            accounts = cursor.fetchall()
            cursor.execute(
                "SELECT card_id, last4 FROM app.core_cards WHERE customer_id = %s ORDER BY card_id",
                (customer_id,),
            )
            cards = cursor.fetchall()
        return {
            "customer_id": customer_id,
            "name": f"{persona.name} Souza",
            "segment": "Personnalité",
            "credit_score": 820,
            "accounts": [
                {"account_id": str(row["account_id"]), "type": str(row["account_type"])}
                for row in accounts
            ],
            "cards": [
                {"card_id": str(row["card_id"]), "last4": str(row["last4"])} for row in cards
            ],
        }

    def get_card_limit(self, customer_id: str, card_id: str) -> dict[str, Any]:
        card = self._card(customer_id, card_id)
        if card is None:
            return _error("CARD_NOT_FOUND")
        return {
            "card_id": card_id,
            "customer_id": customer_id,
            "current_limit": card["current_limit"],
            "currency": "BRL",
            "last4": card["last4"],
            "used_amount": card["used_amount"],
        }

    def get_account_balance(self, customer_id: str, account_id: str) -> dict[str, Any]:
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT available_balance FROM app.core_accounts "
                "WHERE customer_id = %s AND account_id = %s",
                (customer_id, account_id),
            )
            account = cursor.fetchone()
        if account is None:
            return _error("ACCOUNT_NOT_FOUND")
        return {
            "account_id": account_id,
            "customer_id": customer_id,
            "available_balance": account["available_balance"],
            "currency": "BRL",
        }

    def get_card_invoice(self, customer_id: str, card_id: str) -> dict[str, Any]:
        card = self._card(customer_id, card_id)
        if card is None:
            return _error("CARD_NOT_FOUND")
        return {
            "card_id": card_id,
            "customer_id": customer_id,
            "last4": card["last4"],
            "amount": card["invoice_amount"],
            "due_date": str(card["invoice_due_day"]).zfill(2),
            "status": card["invoice_status"],
            "currency": "BRL",
            "updated_at": card["updated_at"].isoformat(),
        }

    def get_account_statement(
        self, customer_id: str, account_id: str, period: str | None = None
    ) -> dict[str, Any]:
        if not self._account_exists(customer_id, account_id):
            return _error("ACCOUNT_NOT_FOUND")
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT transaction_id, description, amount, occurred_at, kind "
                "FROM app.core_transactions WHERE customer_id = %s AND account_id = %s "
                "ORDER BY occurred_at DESC LIMIT 50",
                (customer_id, account_id),
            )
            entries = cursor.fetchall()
        return {
            "account_id": account_id,
            "customer_id": customer_id,
            "period": period,
            "entries": [
                {
                    "transaction_id": row["transaction_id"],
                    "description": row["description"],
                    "amount": row["amount"],
                    "occurred_at": row["occurred_at"].isoformat(),
                    "kind": row["kind"],
                }
                for row in entries
            ],
        }

    def update_card_limit(
        self,
        customer_id: str,
        card_id: str,
        new_limit: Decimal,
        requested_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not requested_by.startswith("user:") or not idempotency_key:
            return _error("SYSTEM_REJECTED")
        if not _valid_limit(new_limit):
            return _error("INVALID_LIMIT")
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT old_limit, new_limit, updated_at FROM app.core_limit_receipts "
                "WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            receipt = cursor.fetchone()
            if receipt is not None:
                return _limit_receipt(card_id, receipt)
            cursor.execute(
                "SELECT current_limit, used_amount FROM app.core_cards "
                "WHERE customer_id = %s AND card_id = %s FOR UPDATE",
                (customer_id, card_id),
            )
            card = cursor.fetchone()
            if card is None:
                return _error("CARD_NOT_FOUND")
            if new_limit < card["used_amount"]:
                return _error("LIMIT_BELOW_USED")
            now = datetime.now(UTC)
            cursor.execute(
                "UPDATE app.core_cards SET current_limit = %s, updated_at = %s "
                "WHERE customer_id = %s AND card_id = %s",
                (new_limit, now, customer_id, card_id),
            )
            cursor.execute(
                "INSERT INTO app.core_limit_receipts "
                "(idempotency_key, customer_id, card_id, old_limit, new_limit, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (idempotency_key, customer_id, card_id, card["current_limit"], new_limit, now),
            )
            return {
                "card_id": card_id,
                "old_limit": card["current_limit"],
                "new_limit": new_limit,
                "updated_at": now.isoformat(),
            }

    def create_pix(
        self,
        from_customer_id: str,
        from_account_id: str,
        recipient_key: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not _valid_pix_key(recipient_key):
            return _error("INVALID_KEY")
        if amount <= 0 or not _at_most_two_decimal_places(amount):
            return _error("INVALID_AMOUNT")
        if not idempotency_key:
            return _error("SYSTEM_REJECTED")
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT transaction_id, amount, recipient_key_masked, executed_at, e2e_id "
                "FROM app.core_pix_receipts WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            receipt = cursor.fetchone()
            if receipt is not None:
                return _pix_receipt(receipt)
            cursor.execute(
                "SELECT available_balance FROM app.core_accounts "
                "WHERE customer_id = %s AND account_id = %s FOR UPDATE",
                (from_customer_id, from_account_id),
            )
            account = cursor.fetchone()
            if account is None:
                return _error("ACCOUNT_NOT_FOUND")
            if amount > account["available_balance"]:
                return _error("INSUFFICIENT_FUNDS")
            now = datetime.now(UTC)
            transaction_id = f"pix-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}"
            e2e_id = f"E60701190{uuid.uuid5(uuid.NAMESPACE_DNS, idempotency_key).hex[:16]}"
            masked_key = _mask_key(recipient_key)
            cursor.execute(
                "UPDATE app.core_accounts SET available_balance = available_balance - %s "
                "WHERE customer_id = %s AND account_id = %s",
                (amount, from_customer_id, from_account_id),
            )
            cursor.execute(
                "INSERT INTO app.core_transactions "
                "(transaction_id, customer_id, account_id, description, amount, kind, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s, 'debit', %s)",
                (
                    transaction_id,
                    from_customer_id,
                    from_account_id,
                    f"PIX enviado — {masked_key}",
                    -amount,
                    now,
                ),
            )
            cursor.execute(
                "INSERT INTO app.core_pix_receipts "
                "(idempotency_key, transaction_id, customer_id, account_id, amount, "
                "recipient_key_masked, executed_at, e2e_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    idempotency_key,
                    transaction_id,
                    from_customer_id,
                    from_account_id,
                    amount,
                    masked_key,
                    now,
                    e2e_id,
                ),
            )
            return {
                "transaction_id": transaction_id,
                "status": "executed",
                "amount": amount,
                "recipient_key_masked": masked_key,
                "executed_at": now.isoformat(),
                "e2e_id": e2e_id,
            }

    def _card(self, customer_id: str, card_id: str) -> dict[str, Any] | None:
        with self._connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT last4, current_limit, used_amount, invoice_amount, invoice_due_day, "
                "invoice_status, updated_at FROM app.core_cards "
                "WHERE customer_id = %s AND card_id = %s",
                (customer_id, card_id),
            )
            return cursor.fetchone()

    def _account_exists(self, customer_id: str, account_id: str) -> bool:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM app.core_accounts WHERE customer_id = %s AND account_id = %s",
                (customer_id, account_id),
            )
            return cursor.fetchone() is not None

    def _connection(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._database_url)


def _persona(customer_id: str) -> Any | None:
    return next((persona for persona in DEMO_PERSONAS if persona.customer_id == customer_id), None)


def _error(code: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code}}


def _valid_limit(value: Decimal) -> bool:
    return value > 0 and _at_most_two_decimal_places(value) and value % Decimal("100") == 0


def _valid_pix_key(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return bool(
        EMAIL_PIX_KEY.fullmatch(value)
        or PHONE_PIX_KEY.fullmatch(value)
        or len(digits) in {11, 14}
        or _is_uuid(value)
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _mask_key(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain[:1]}**.com"
    return f"{value[:3]}***"


def _at_most_two_decimal_places(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and exponent >= -2


def _limit_receipt(card_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": card_id,
        "old_limit": row["old_limit"],
        "new_limit": row["new_limit"],
        "updated_at": row["updated_at"].isoformat(),
    }


def _pix_receipt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": row["transaction_id"],
        "status": "executed",
        "amount": row["amount"],
        "recipient_key_masked": row["recipient_key_masked"],
        "executed_at": row["executed_at"].isoformat(),
        "e2e_id": row["e2e_id"],
    }
