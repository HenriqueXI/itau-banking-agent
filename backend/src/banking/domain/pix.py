"""PIX transfer rules (BR-3), kept free of persistence and framework code."""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from shared.logging.masking import mask_pii

EMAIL_KEY = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_KEY = re.compile(r"^\+[1-9]\d{7,14}$")


class PixValidationError(ValueError):
    pass


def validate_pix_key(value: str) -> str:
    """Accept the four key types supported by the simulated banking contract."""
    value = value.strip()
    digits = re.sub(r"\D", "", value)
    valid = bool(
        EMAIL_KEY.fullmatch(value)
        or PHONE_KEY.fullmatch(value)
        or len(digits) in {11, 14}
        or _is_evp(value)
    )
    if not valid:
        raise PixValidationError("invalid PIX recipient key")
    return value


def validate_amount(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if value <= 0 or not isinstance(exponent, int) or exponent < -2:
        raise PixValidationError("PIX amount must be positive with at most two decimals")
    return value


def _is_evp(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, kw_only=True)
class PixTransfer:
    """A persisted money-movement intent and its immutable execution values."""

    operation_hash: str
    customer_id: str
    account_id: str
    recipient_key: str
    recipient_key_masked: str
    amount: Decimal
    local_day: date

    @classmethod
    def create(
        cls,
        *,
        operation_hash: str,
        customer_id: str,
        account_id: str,
        recipient_key: str,
        amount: Decimal,
        local_day: date,
    ) -> "PixTransfer":
        return cls(
            operation_hash=operation_hash,
            customer_id=customer_id,
            account_id=account_id,
            recipient_key=validate_pix_key(recipient_key),
            recipient_key_masked=mask_pii(recipient_key),
            amount=validate_amount(amount),
            local_day=local_day,
        )
