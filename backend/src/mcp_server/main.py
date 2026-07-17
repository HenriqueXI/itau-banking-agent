"""FastMCP bootstrap for the PostgreSQL-owned core-banking process."""

import os
from decimal import Decimal
from typing import Any, Protocol

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server.postgres_core import PostgresCoreBanking
from mcp_server.simulator import CoreBankingSimulator


class CoreBankingService(Protocol):
    def get_customer_profile(self, customer_id: str) -> dict[str, Any]: ...

    def get_card_limit(self, customer_id: str, card_id: str) -> dict[str, Any]: ...

    def get_account_balance(self, customer_id: str, account_id: str) -> dict[str, Any]: ...

    def get_card_invoice(self, customer_id: str, card_id: str) -> dict[str, Any]: ...

    def get_account_statement(
        self, customer_id: str, account_id: str, period: str | None = None
    ) -> dict[str, Any]: ...

    def update_card_limit(
        self,
        customer_id: str,
        card_id: str,
        new_limit: Decimal,
        requested_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def create_pix(
        self,
        from_customer_id: str,
        from_account_id: str,
        recipient_key: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


def _runtime_core() -> CoreBankingService:
    database_url = os.getenv("MCP_DATABASE_URL")
    if database_url:
        return PostgresCoreBanking(database_url)
    # A database-less server is useful only to the isolated FastMCP contract
    # fixture. Compose always supplies MCP_DATABASE_URL.
    return CoreBankingSimulator()


def create_server(core: CoreBankingService | None = None) -> FastMCP:
    core = core or _runtime_core()
    server = FastMCP(
        "itau-core-banking",
        host="0.0.0.0",  # nosec B104 - exposed only on the internal compose network
        port=8080,
        streamable_http_path="/mcp",
        json_response=True,
    )

    @server.tool()
    def get_customer_profile(customer_id: str) -> dict[str, Any]:
        return core.get_customer_profile(customer_id)

    @server.tool()
    def get_card_limit(customer_id: str, card_id: str) -> dict[str, Any]:
        return core.get_card_limit(customer_id, card_id)

    @server.tool()
    def get_account_balance(customer_id: str, account_id: str) -> dict[str, Any]:
        return core.get_account_balance(customer_id, account_id)

    @server.tool()
    def get_card_invoice(customer_id: str, card_id: str) -> dict[str, Any]:
        return core.get_card_invoice(customer_id, card_id)

    @server.tool()
    def get_account_statement(
        customer_id: str, account_id: str, period: str | None = None
    ) -> dict[str, Any]:
        return core.get_account_statement(customer_id, account_id, period)

    @server.tool()
    def update_card_limit(
        customer_id: str,
        card_id: str,
        new_limit: Decimal,
        requested_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return core.update_card_limit(
            customer_id, card_id, new_limit, requested_by, idempotency_key
        )

    @server.tool()
    def create_pix(
        from_customer_id: str,
        from_account_id: str,
        recipient_key: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return core.create_pix(
            from_customer_id,
            from_account_id,
            recipient_key,
            amount,
            idempotency_key,
        )

    @server.custom_route(  # type: ignore[untyped-decorator]
        "/health", methods=["GET"], include_in_schema=False
    )
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server


def main() -> None:
    create_server().run(transport="streamable-http")


def create_app() -> Any:
    """Uvicorn factory: each process owns one MCP session manager."""
    return create_server().streamable_http_app()


if __name__ == "__main__":
    main()
