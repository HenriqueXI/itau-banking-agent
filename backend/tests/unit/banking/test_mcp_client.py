from decimal import Decimal
from typing import Any

import pytest
from tests.fakes import RecordingTracer

from banking.adapters.outbound.mcp_client import McpBankingSystemsClient
from banking.domain.errors import (
    CustomerNotFound,
    MalformedBankingResponse,
    SystemUnavailable,
)
from banking.domain.values import PixCommand
from shared.application.ports.tracer import TraceSpec
from shared.telemetry import spans


class FakeTransport:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        self.calls.append((tool, arguments, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ping(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


async def test_profile_maps_to_typed_value_and_uses_read_timeout() -> None:
    transport = FakeTransport(
        [
            {
                "customer_id": "123",
                "name": "Ana Souza",
                "segment": "Personnalité",
                "credit_score": 820,
                "accounts": [{"account_id": "acc-1", "type": "checking"}],
            }
        ]
    )
    client = McpBankingSystemsClient(transport=transport)

    profile = await client.get_customer_profile("123")

    assert profile.customer_id == "123"
    assert profile.credit_score == 820
    assert transport.calls == [("get_customer_profile", {"customer_id": "123"}, 5.0)]


async def test_server_error_code_maps_to_typed_error() -> None:
    client = McpBankingSystemsClient(
        transport=FakeTransport([{"error": {"code": "CUSTOMER_NOT_FOUND"}}])
    )

    with pytest.raises(CustomerNotFound):
        await client.get_customer_profile("missing")


async def test_malformed_response_never_becomes_a_partial_domain_object() -> None:
    client = McpBankingSystemsClient(transport=FakeTransport([{"customer_id": "123"}]))

    with pytest.raises(MalformedBankingResponse):
        await client.get_customer_profile("123")


async def test_write_uses_ten_second_timeout_without_retry() -> None:
    transport = FakeTransport(
        [
            {
                "transaction_id": "pix-1",
                "status": "executed",
                "amount": "200.00",
                "recipient_key_masked": "c***e@p**.com",
                "executed_at": "2026-07-15T12:00:00+00:00",
                "e2e_id": "E60701190TEST",
            }
        ]
    )
    client = McpBankingSystemsClient(transport=transport)

    await client.execute_pix(
        PixCommand(
            from_customer_id="123",
            from_account_id="acc-1",
            recipient_key="chave@pix.com",
            amount=Decimal("200.00"),
            idempotency_key="operation-123",
        )
    )

    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "create_pix"
    assert transport.calls[0][2] == 10.0


async def test_mcp_write_uses_the_stable_tool_span_name() -> None:
    transport = FakeTransport(
        [
            {
                "transaction_id": "pix-1",
                "status": "executed",
                "amount": "200.00",
                "recipient_key_masked": "c***",
                "executed_at": "2026-07-15T12:00:00+00:00",
                "e2e_id": "E1",
            }
        ]
    )
    client = McpBankingSystemsClient(transport=transport)
    tracer = RecordingTracer()

    with tracer.trace(TraceSpec(name=spans.TURN, trace_id="trace-1")):
        await client.execute_pix(
            PixCommand(
                from_customer_id="123",
                from_account_id="acc-1",
                recipient_key="chave@pix.com",
                amount=Decimal("200.00"),
                idempotency_key="operation-123",
            )
        )

    assert f"{spans.TURN}/{spans.tool_span('create_pix')}" in tracer.paths()


async def test_circuit_breaker_opens_after_five_transport_failures() -> None:
    transport = FakeTransport([ConnectionError("down")] * 5)
    client = McpBankingSystemsClient(transport=transport)

    for _ in range(5):
        with pytest.raises(SystemUnavailable):
            await client.get_customer_profile("123")

    with pytest.raises(SystemUnavailable, match="circuit breaker"):
        await client.get_customer_profile("123")
    assert len(transport.calls) == 5
