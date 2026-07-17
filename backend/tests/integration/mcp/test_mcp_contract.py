import asyncio
import socket
import threading
from decimal import Decimal

import httpx
import pytest
import uvicorn

from banking.adapters.outbound.mcp_client import McpBankingSystemsClient
from banking.domain.errors import CustomerNotFound
from banking.domain.values import LimitUpdateCommand, PixCommand


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def mcp_url() -> str:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            "mcp_server.main:create_app",
            host="127.0.0.1",
            port=port,
            log_level="error",
            factory=True,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(30):
                try:
                    if (await client.get(f"{base_url}/health")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("MCP server did not start")
        yield f"{base_url}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.integration
async def test_client_and_fastmcp_server_obey_the_banking_tool_contracts(mcp_url: str) -> None:
    client = McpBankingSystemsClient(url=mcp_url)
    try:
        await client.ping()
        profile = await client.get_customer_profile("123")
        limit = await client.get_card_limit("123", "card-1")
        balance = await client.get_account_balance("123", "acc-1")
        invoice = await client.get_card_invoice("123", "card-1")
        statement = await client.get_account_statement("123", "acc-1")
        update = await client.update_card_limit(
            LimitUpdateCommand(
                customer_id="123",
                card_id="card-1",
                new_limit=Decimal("6000"),
                requested_by="user:ana@demo",
                idempotency_key="mcp-contract-limit-1",
            )
        )
        command = PixCommand(
            from_customer_id="123",
            from_account_id="acc-1",
            recipient_key="chave@pix.com",
            amount=Decimal("200.00"),
            idempotency_key="mcp-contract-pix-1",
        )
        first_pix = await client.execute_pix(command)
        second_pix = await client.execute_pix(command)

        assert profile.segment == "Personnalité"
        assert [card.card_id for card in profile.cards] == ["card-1", "card-2"]
        assert limit.current_limit == Decimal("5000.00")
        assert limit.used_amount == Decimal("1834.90")
        assert balance == Decimal("28412.37")
        assert invoice.amount == Decimal("1834.90")
        assert statement.entries
        assert update.new_limit == Decimal("6000")
        assert first_pix == second_pix
    finally:
        await client.aclose()


@pytest.mark.integration
async def test_unknown_customer_is_a_typed_contract_error(mcp_url: str) -> None:
    client = McpBankingSystemsClient(url=mcp_url)
    try:
        with pytest.raises(CustomerNotFound):
            await client.get_customer_profile("does-not-exist")
    finally:
        await client.aclose()
