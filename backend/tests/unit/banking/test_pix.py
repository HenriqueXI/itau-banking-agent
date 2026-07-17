from datetime import date
from decimal import Decimal

import pytest

from banking.domain.pix import PixTransfer, PixValidationError, validate_pix_key


@pytest.mark.parametrize(
    "key",
    [
        # email
        "ana@example.com",
        # phone (E.164)
        "+5511999999999",
        # CPF — formatted and raw 11 digits
        "123.456.789-09",
        "12345678909",
        # CNPJ — formatted and raw 14 digits
        "12.345.678/0001-95",
        "12345678000195",
        # EVP (uuid)
        "c6d38c4e-6e6b-4d5e-a5a2-2c3bb9c13c12",
    ],
)
def test_accepts_supported_pix_key_types(key: str) -> None:
    assert validate_pix_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "not a key",
        "1234567890",  # 10 digits: neither CPF nor CNPJ
        "123456789012345",  # 15 digits
        "ana@invalid",  # email without a domain dot
        "foo@bar",
        "+0511999999999",  # E.164 cannot start with 0
    ],
)
def test_rejects_invalid_pix_keys(key: str) -> None:
    with pytest.raises(PixValidationError):
        validate_pix_key(key)


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01"), Decimal("1.001")])
def test_rejects_non_positive_or_fractional_cent_amounts(amount: Decimal) -> None:
    with pytest.raises(PixValidationError):
        PixTransfer.create(
            operation_hash="hash",
            customer_id="123",
            account_id="acc-1",
            recipient_key="ana@example.com",
            amount=amount,
            local_day=date(2026, 7, 15),
        )


def test_masks_recipient_key_on_transfer() -> None:
    transfer = PixTransfer.create(
        operation_hash="hash",
        customer_id="123",
        account_id="acc-1",
        recipient_key="ana@example.com",
        amount=Decimal("1000.00"),
        local_day=date(2026, 7, 15),
    )
    assert transfer.recipient_key_masked != transfer.recipient_key
    assert "@example.com" not in transfer.recipient_key_masked
