"""Static demo-persona reference resolution stays deterministic and bounded."""

import pytest

from conversation.adapters.outbound.demo_customer_reference import DemoCustomerReferenceResolver


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("Ana", "123"),
        ("  ANA   SOUZA ", "123"),
        ("Bruno", "456"),
        ("Carla Souza", "789"),
        ("123", "123"),
        ("456", "456"),
        ("789", "789"),
        ("Cliente desconhecido", None),
    ],
)
async def test_resolves_only_seeded_demo_personas(reference: str, expected: str | None) -> None:
    assert await DemoCustomerReferenceResolver().resolve(reference) == expected
