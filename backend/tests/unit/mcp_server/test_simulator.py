from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from mcp_server.simulator import CoreBankingSimulator


def test_profile_matches_the_demo_persona_seed() -> None:
    simulator = CoreBankingSimulator()

    result = simulator.get_customer_profile("123")

    assert result == {
        "customer_id": "123",
        "name": "Ana Souza",
        "segment": "Personnalité",
        "credit_score": 820,
        "accounts": [{"account_id": "acc-1", "type": "checking"}],
        "cards": [
            {"card_id": "card-1", "last4": "4242"},
            {"card_id": "card-2", "last4": "8888"},
        ],
    }


def test_pix_is_idempotent_and_executes_once() -> None:
    simulator = CoreBankingSimulator()
    payload = {
        "from_customer_id": "123",
        "from_account_id": "acc-1",
        "recipient_key": "chave@pix.com",
        "amount": Decimal("200.00"),
        "idempotency_key": "operation-123",
    }

    first = simulator.create_pix(**payload)
    second = simulator.create_pix(**payload)

    assert first == second
    assert first["status"] == "executed"
    # The call log records every request received (zero-call proofs read it);
    # executions count distinct receipts.
    assert [entry.tool for entry in simulator.call_log] == ["create_pix", "create_pix"]
    assert simulator.pix_executions == 1


def test_pix_rejects_invalid_key_and_non_positive_amount() -> None:
    simulator = CoreBankingSimulator()

    invalid_key = simulator.create_pix(
        from_customer_id="123",
        from_account_id="acc-1",
        recipient_key="not-a-pix-key",
        amount=Decimal("1.00"),
        idempotency_key="operation-124",
    )
    invalid_amount = simulator.create_pix(
        from_customer_id="123",
        from_account_id="acc-1",
        recipient_key="chave@pix.com",
        amount=Decimal("0"),
        idempotency_key="operation-125",
    )

    assert invalid_key == {"error": {"code": "INVALID_KEY"}}
    assert invalid_amount == {"error": {"code": "INVALID_AMOUNT"}}


def test_pix_rejects_an_amount_above_the_available_balance() -> None:
    simulator = CoreBankingSimulator()

    result = simulator.create_pix(
        from_customer_id="123",
        from_account_id="acc-1",
        recipient_key="chave@pix.com",
        amount=Decimal("50000.01"),
        idempotency_key="operation-over-balance",
    )

    assert result == {"error": {"code": "INSUFFICIENT_FUNDS"}}


def test_concurrent_pix_with_one_idempotency_key_executes_once() -> None:
    simulator = CoreBankingSimulator()
    payload = {
        "from_customer_id": "123",
        "from_account_id": "acc-1",
        "recipient_key": "chave@pix.com",
        "amount": Decimal("200.00"),
        "idempotency_key": "operation-concurrent",
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = list(executor.map(lambda _: simulator.create_pix(**payload), range(8)))

    assert all(receipt == receipts[0] for receipt in receipts)
    assert simulator.pix_executions == 1


def test_limit_update_rejects_bad_values_and_records_actor() -> None:
    simulator = CoreBankingSimulator()

    rejected = simulator.update_card_limit(
        customer_id="123",
        card_id="card-1",
        new_limit=Decimal("5010.00"),
        requested_by="user:ana@demo",
        idempotency_key="limit-invalid",
    )
    updated = simulator.update_card_limit(
        customer_id="123",
        card_id="card-1",
        new_limit=Decimal("6000.00"),
        requested_by="user:ana@demo",
        idempotency_key="limit-valid",
    )

    assert rejected == {"error": {"code": "INVALID_LIMIT"}}
    assert updated["new_limit"] == Decimal("6000.00")
    assert simulator.call_log[-1].requested_by == "user:ana@demo"


def test_limit_update_rejects_a_limit_below_the_already_used_amount() -> None:
    simulator = CoreBankingSimulator()

    result = simulator.update_card_limit(
        customer_id="123",
        card_id="card-1",
        new_limit=Decimal("1800.00"),
        requested_by="user:ana@demo",
        idempotency_key="limit-below-used",
    )

    assert result == {"error": {"code": "LIMIT_BELOW_USED"}}
    assert simulator.get_card_limit("123", "card-1")["current_limit"] == Decimal("5000.00")
