"""In-memory, deterministic core-banking simulator behind the MCP process."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import Any

from shared.demo_personas import DEMO_PERSONAS

EMAIL_PIX_KEY = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PIX_KEY = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True, kw_only=True)
class CallLogEntry:
    tool: str
    requested_by: str | None = None


class CoreBankingSimulator:
    """State belongs to this process; a lock makes PIX idempotency atomic."""

    def __init__(self) -> None:
        self._lock = RLock()
        # The simulator deliberately owns this read model.  The HTTP backend
        # only reaches it through BankingSystemsPort; PostgreSQL persistence is
        # introduced by the core-banking repository without changing this
        # tool contract.
        self._cards: dict[tuple[str, str], dict[str, Any]] = {
            ("123", "card-1"): {
                "last4": "4242",
                "limit": Decimal("5000.00"),
                "used": Decimal("1834.90"),
                "invoice": Decimal("1834.90"),
                "due_date": "10",
            },
            ("123", "card-2"): {
                "last4": "8888",
                "limit": Decimal("3000.00"),
                "used": Decimal("420.00"),
                "invoice": Decimal("420.00"),
                "due_date": "20",
            },
            ("456", "card-3"): {
                "last4": "8801",
                "limit": Decimal("25000.00"),
                "used": Decimal("6420.30"),
                "invoice": Decimal("6420.30"),
                "due_date": "15",
            },
            ("789", "card-4"): {
                "last4": "1177",
                "limit": Decimal("8000.00"),
                "used": Decimal("920.15"),
                "invoice": Decimal("920.15"),
                "due_date": "05",
            },
        }
        self._balances: dict[tuple[str, str], Decimal] = {
            # UC-4 deliberately exercises a R$ 20.000 PIX after step-up.
            # Keep the official demo persona funded for that critical flow.
            ("123", "acc-1"): Decimal("28412.37"),
            ("456", "acc-2"): Decimal("23980.15"),
            ("789", "acc-3"): Decimal("5120.44"),
        }
        self._statements: dict[tuple[str, str], list[dict[str, Any]]] = {
            ("123", "acc-1"): [
                {
                    "transaction_id": "t1",
                    "description": "Supermercado Pão de Açúcar",
                    "amount": Decimal("-284.52"),
                    "kind": "debit",
                },
                {
                    "transaction_id": "t2",
                    "description": "PIX recebido — Maria S.",
                    "amount": Decimal("350.00"),
                    "kind": "credit",
                },
                {
                    "transaction_id": "t3",
                    "description": "Restaurante Coco Bambu",
                    "amount": Decimal("-187.40"),
                    "kind": "debit",
                },
            ],
        }
        self._limit_receipts: dict[str, dict[str, Any]] = {}
        self._pix_receipts: dict[str, dict[str, Any]] = {}
        self.call_log: list[CallLogEntry] = []

    @property
    def pix_executions(self) -> int:
        """Distinct PIX actually executed (receipts), regardless of replays."""
        return len(self._pix_receipts)

    @property
    def limit_updates(self) -> int:
        """Distinct limit changes actually applied, regardless of replays."""
        return len(self._limit_receipts)

    def get_customer_profile(self, customer_id: str) -> dict[str, Any]:
        # Logged at entry: the call log proves which requests *reached* the
        # banking system (UC-3 zero-call proof), not which ones succeeded.
        self.call_log.append(CallLogEntry(tool="get_customer_profile"))
        persona = next((item for item in DEMO_PERSONAS if item.customer_id == customer_id), None)
        if persona is None:
            return _error("CUSTOMER_NOT_FOUND")
        accounts = {"123": "acc-1", "456": "acc-2", "789": "acc-3"}
        return {
            "customer_id": customer_id,
            "name": f"{persona.name} Souza",
            "segment": "Personnalité",
            "credit_score": 820,
            "accounts": [{"account_id": accounts[customer_id], "type": "checking"}],
            "cards": [
                {"card_id": card_id, "last4": card["last4"]}
                for (owner, card_id), card in self._cards.items()
                if owner == customer_id
            ],
        }

    def get_card_limit(self, customer_id: str, card_id: str) -> dict[str, Any]:
        self.call_log.append(CallLogEntry(tool="get_card_limit"))
        card = self._cards.get((customer_id, card_id))
        if card is None:
            return _error("CARD_NOT_FOUND")
        return {
            "card_id": card_id,
            "customer_id": customer_id,
            "current_limit": card["limit"],
            "currency": "BRL",
            "last4": card["last4"],
            "used_amount": card["used"],
        }

    def get_account_balance(self, customer_id: str, account_id: str) -> dict[str, Any]:
        self.call_log.append(CallLogEntry(tool="get_account_balance"))
        balance = self._balances.get((customer_id, account_id))
        if balance is None:
            return _error("ACCOUNT_NOT_FOUND")
        return {
            "account_id": account_id,
            "customer_id": customer_id,
            "available_balance": balance,
            "currency": "BRL",
        }

    def get_card_invoice(self, customer_id: str, card_id: str) -> dict[str, Any]:
        self.call_log.append(CallLogEntry(tool="get_card_invoice"))
        card = self._cards.get((customer_id, card_id))
        if card is None:
            return _error("CARD_NOT_FOUND")
        return {
            "card_id": card_id,
            "customer_id": customer_id,
            "last4": card["last4"],
            "amount": card["invoice"],
            "due_date": card["due_date"],
            "status": "OPEN",
            "currency": "BRL",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def get_account_statement(
        self, customer_id: str, account_id: str, period: str | None = None
    ) -> dict[str, Any]:
        self.call_log.append(CallLogEntry(tool="get_account_statement"))
        if (customer_id, account_id) not in self._balances:
            return _error("ACCOUNT_NOT_FOUND")
        now = datetime.now(UTC).isoformat()
        return {
            "account_id": account_id,
            "customer_id": customer_id,
            "period": period,
            "entries": [
                {**entry, "occurred_at": now}
                for entry in self._statements.get((customer_id, account_id), [])
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
        self.call_log.append(CallLogEntry(tool="update_card_limit", requested_by=requested_by))
        card = self._cards.get((customer_id, card_id))
        if card is None:
            return _error("CARD_NOT_FOUND")
        if not requested_by.startswith("user:"):
            return _error("SYSTEM_REJECTED")
        if not idempotency_key:
            return _error("SYSTEM_REJECTED")
        if (
            new_limit <= 0
            or not _at_most_two_decimal_places(new_limit)
            or new_limit % Decimal("100")
        ):
            return _error("INVALID_LIMIT")
        with self._lock:
            if idempotency_key in self._limit_receipts:
                return self._limit_receipts[idempotency_key]
            old_limit = card["limit"]
            if new_limit < card["used"]:
                return _error("LIMIT_BELOW_USED")
            card["limit"] = new_limit
        receipt = {
            "card_id": card_id,
            "old_limit": old_limit,
            "new_limit": new_limit,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._limit_receipts[idempotency_key] = receipt
        return receipt

    def create_pix(
        self,
        from_customer_id: str,
        from_account_id: str,
        recipient_key: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.call_log.append(CallLogEntry(tool="create_pix"))
        if (from_customer_id, from_account_id) not in self._balances:
            return _error("ACCOUNT_NOT_FOUND")
        if not _valid_pix_key(recipient_key):
            return _error("INVALID_KEY")
        if amount <= 0 or not _at_most_two_decimal_places(amount):
            return _error("INVALID_AMOUNT")
        if amount > self._balances[(from_customer_id, from_account_id)]:
            return _error("INSUFFICIENT_FUNDS")
        if not idempotency_key:
            return _error("SYSTEM_REJECTED")
        with self._lock:
            if idempotency_key in self._pix_receipts:
                return self._pix_receipts[idempotency_key]
            receipt = {
                "transaction_id": f"pix-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}",
                "status": "executed",
                "amount": amount,
                "recipient_key_masked": _mask_key(recipient_key),
                "executed_at": datetime.now(UTC).isoformat(),
                "e2e_id": f"E60701190{uuid.uuid5(uuid.NAMESPACE_DNS, idempotency_key).hex[:16]}",
            }
            self._pix_receipts[idempotency_key] = receipt
            self._balances[(from_customer_id, from_account_id)] -= amount
            return receipt


def _error(code: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code}}


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
