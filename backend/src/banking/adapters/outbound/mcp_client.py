"""MCP implementation of :class:`BankingSystemsPort` (ADR-009)."""

import asyncio
from contextlib import AsyncExitStack, nullcontext
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from banking.application.ports.banking_systems import BankingSystemsPort
from banking.domain.errors import (
    AccountNotFound,
    CardNotFound,
    CustomerNotFound,
    InsufficientFunds,
    InvalidBankingRequest,
    MalformedBankingResponse,
    SystemUnavailable,
)
from banking.domain.values import (
    AccountStatement,
    BankAccount,
    BankCard,
    CardInvoice,
    CardLimit,
    CustomerProfile,
    LimitUpdateCommand,
    LimitUpdateReceipt,
    PixCommand,
    PixReceipt,
    StatementEntry,
)
from shared.application.ports.tracer import current_scope
from shared.logging.masking import mask_mapping
from shared.telemetry.spans import tool_span

READ_TIMEOUT_SECONDS = 5.0
WRITE_TIMEOUT_SECONDS = 10.0
FAILURE_THRESHOLD = 5
EXPECTED_TOOLS = frozenset(
    {
        "get_customer_profile",
        "get_card_limit",
        "get_account_balance",
        "get_card_invoice",
        "get_account_statement",
        "update_card_limit",
        "create_pix",
    }
)
EXPECTED_REQUIRED_ARGUMENTS = {
    "get_customer_profile": frozenset({"customer_id"}),
    "get_card_limit": frozenset({"customer_id", "card_id"}),
    "get_account_balance": frozenset({"customer_id", "account_id"}),
    "get_card_invoice": frozenset({"customer_id", "card_id"}),
    "get_account_statement": frozenset({"customer_id", "account_id"}),
    "update_card_limit": frozenset(
        {"customer_id", "card_id", "new_limit", "requested_by", "idempotency_key"}
    ),
    "create_pix": frozenset(
        {"from_customer_id", "from_account_id", "recipient_key", "amount", "idempotency_key"}
    ),
}


class McpTransport(Protocol):
    async def call(
        self, tool: str, arguments: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]: ...

    async def ping(self) -> None: ...

    async def aclose(self) -> None: ...


class McpSdkTransport:
    """One initialized streamable-HTTP session, reused until it breaks."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> ClientSession:
        async with self._lock:
            if self._session is not None:
                return self._session
            stack = AsyncExitStack()
            try:
                streams = await stack.enter_async_context(streamable_http_client(self._url))
                read_stream, write_stream, _ = streams
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
            except Exception:
                await stack.aclose()
                raise
            self._stack = stack
            self._session = session
            return session

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        result = await asyncio.wait_for(
            session.call_tool(tool, arguments=arguments), timeout=timeout_seconds
        )
        if result.isError:
            raise MalformedBankingResponse(f"MCP tool {tool!r} returned a protocol error")
        if isinstance(result.structuredContent, dict):
            return result.structuredContent
        if len(result.content) == 1 and isinstance(result.content[0], TextContent):
            # ``json_response=True`` should always provide structured content. A
            # text-only result is schema drift, never a partial domain object.
            raise MalformedBankingResponse(f"MCP tool {tool!r} omitted structured content")
        raise MalformedBankingResponse(f"MCP tool {tool!r} returned an invalid response")

    async def ping(self) -> None:
        session = await self._ensure_session()
        result = await asyncio.wait_for(session.list_tools(), timeout=READ_TIMEOUT_SECONDS)
        tools = {tool.name: tool for tool in result.tools}
        if set(tools) != EXPECTED_TOOLS or any(
            frozenset(tools[name].inputSchema.get("required", [])) != required
            for name, required in EXPECTED_REQUIRED_ARGUMENTS.items()
        ):
            raise MalformedBankingResponse("MCP tool schema drift detected")

    async def aclose(self) -> None:
        async with self._lock:
            if self._stack is not None:
                await self._stack.aclose()
            self._stack = None
            self._session = None


class McpBankingSystemsClient(BankingSystemsPort):
    """Typed, timeout-bounded adapter with a fail-fast circuit breaker."""

    def __init__(self, *, url: str | None = None, transport: McpTransport | None = None) -> None:
        if transport is None and url is None:
            raise ValueError("url or transport is required")
        self._transport = transport or McpSdkTransport(url or "")
        self._consecutive_failures = 0
        self._breaker_open = False
        self._lock = asyncio.Lock()

    async def get_customer_profile(self, customer_id: str) -> CustomerProfile:
        payload = await self._call(
            "get_customer_profile", {"customer_id": customer_id}, READ_TIMEOUT_SECONDS
        )
        return CustomerProfile(
            customer_id=_str(payload, "customer_id"),
            name=_str(payload, "name"),
            segment=_str(payload, "segment"),
            credit_score=_int(payload, "credit_score"),
            accounts=tuple(
                BankAccount(account_id=_str(account, "account_id"), type=_str(account, "type"))
                for account in _list(payload, "accounts")
            ),
            cards=tuple(
                BankCard(card_id=_str(card, "card_id"), last4=_str(card, "last4"))
                for card in _optional_list(payload, "cards")
            ),
        )

    async def get_card_limit(self, customer_id: str, card_id: str) -> CardLimit:
        payload = await self._call(
            "get_card_limit", {"customer_id": customer_id, "card_id": card_id}, READ_TIMEOUT_SECONDS
        )
        return CardLimit(
            card_id=_str(payload, "card_id"),
            customer_id=_str(payload, "customer_id"),
            current_limit=_decimal(payload, "current_limit"),
            currency=_str(payload, "currency"),
            last4=_str(payload, "last4"),
            used_amount=_decimal(payload, "used_amount"),
        )

    async def get_account_balance(self, customer_id: str, account_id: str) -> Decimal:
        payload = await self._call(
            "get_account_balance",
            {"customer_id": customer_id, "account_id": account_id},
            READ_TIMEOUT_SECONDS,
        )
        return _decimal(payload, "available_balance")

    async def get_card_invoice(self, customer_id: str, card_id: str) -> CardInvoice:
        payload = await self._call(
            "get_card_invoice",
            {"customer_id": customer_id, "card_id": card_id},
            READ_TIMEOUT_SECONDS,
        )
        return CardInvoice(
            card_id=_str(payload, "card_id"),
            customer_id=_str(payload, "customer_id"),
            last4=_str(payload, "last4"),
            amount=_decimal(payload, "amount"),
            due_date=_str(payload, "due_date"),
            status=_str(payload, "status"),
            currency=_str(payload, "currency"),
            updated_at=_datetime(payload, "updated_at"),
        )

    async def get_account_statement(
        self, customer_id: str, account_id: str, period: str | None = None
    ) -> AccountStatement:
        arguments: dict[str, Any] = {"customer_id": customer_id, "account_id": account_id}
        if period is not None:
            arguments["period"] = period
        payload = await self._call("get_account_statement", arguments, READ_TIMEOUT_SECONDS)
        return AccountStatement(
            account_id=_str(payload, "account_id"),
            customer_id=_str(payload, "customer_id"),
            entries=tuple(
                StatementEntry(
                    transaction_id=_str(entry, "transaction_id"),
                    description=_str(entry, "description"),
                    amount=_decimal(entry, "amount"),
                    occurred_at=_datetime(entry, "occurred_at"),
                    kind=_str(entry, "kind"),
                )
                for entry in _list(payload, "entries")
            ),
        )

    async def update_card_limit(self, command: LimitUpdateCommand) -> LimitUpdateReceipt:
        payload = await self._call(
            "update_card_limit",
            {
                "customer_id": command.customer_id,
                "card_id": command.card_id,
                "new_limit": str(command.new_limit),
                "requested_by": command.requested_by,
                "idempotency_key": command.idempotency_key,
            },
            WRITE_TIMEOUT_SECONDS,
        )
        return LimitUpdateReceipt(
            card_id=_str(payload, "card_id"),
            old_limit=_decimal(payload, "old_limit"),
            new_limit=_decimal(payload, "new_limit"),
            updated_at=_datetime(payload, "updated_at"),
        )

    async def execute_pix(self, command: PixCommand) -> PixReceipt:
        payload = await self._call(
            "create_pix",
            {
                "from_customer_id": command.from_customer_id,
                "from_account_id": command.from_account_id,
                "recipient_key": command.recipient_key,
                "amount": str(command.amount),
                "idempotency_key": command.idempotency_key,
            },
            WRITE_TIMEOUT_SECONDS,
        )
        return PixReceipt(
            transaction_id=_str(payload, "transaction_id"),
            status=_str(payload, "status"),
            amount=_decimal(payload, "amount"),
            recipient_key_masked=_str(payload, "recipient_key_masked"),
            executed_at=_datetime(payload, "executed_at"),
            e2e_id=_str(payload, "e2e_id"),
        )

    async def ping(self) -> None:
        await self._guarded(self._transport.ping, "mcp.ping", {})

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def _call(
        self, tool: str, arguments: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        async def invoke() -> dict[str, Any]:
            return await self._transport.call(tool, arguments, timeout_seconds)

        payload = await self._guarded(invoke, tool_span(tool), arguments)
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            _raise_server_error(error["code"])
        return payload

    async def _guarded(self, operation: Any, span_name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            if self._breaker_open:
                raise SystemUnavailable("MCP circuit breaker is open")
        scope = current_scope()
        context = scope.span(span_name, input=mask_mapping(arguments)) if scope else nullcontext()
        try:
            with context:
                result = await operation()
        except (
            CustomerNotFound,
            CardNotFound,
            AccountNotFound,
            InvalidBankingRequest,
            InsufficientFunds,
        ):
            raise
        except MalformedBankingResponse:
            await self._record_failure()
            raise
        except Exception as exc:
            await self._record_failure()
            if isinstance(exc, SystemUnavailable):
                raise
            raise SystemUnavailable("MCP banking system is unavailable") from exc
        async with self._lock:
            self._consecutive_failures = 0
        return result

    async def _record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= FAILURE_THRESHOLD:
                self._breaker_open = True


def _raise_server_error(code: str) -> None:
    errors = {
        "CUSTOMER_NOT_FOUND": CustomerNotFound,
        "CARD_NOT_FOUND": CardNotFound,
        "ACCOUNT_NOT_FOUND": AccountNotFound,
        "INSUFFICIENT_FUNDS": InsufficientFunds,
        "INVALID_LIMIT": InvalidBankingRequest,
        "INVALID_AMOUNT": InvalidBankingRequest,
        "INVALID_KEY": InvalidBankingRequest,
        "LIMIT_BELOW_USED": InvalidBankingRequest,
        "SYSTEM_REJECTED": InvalidBankingRequest,
    }
    error = errors.get(code, MalformedBankingResponse)
    raise error(f"MCP banking error: {code}")


def _str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MalformedBankingResponse(f"missing or invalid {key}")
    return value


def _int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedBankingResponse(f"missing or invalid {key}")
    return value


def _list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise MalformedBankingResponse(f"missing or invalid {key}")
    return value


def _optional_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if key not in payload:
        return []
    return _list(payload, key)


def _decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = payload.get(key)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MalformedBankingResponse(f"missing or invalid {key}") from exc


def _datetime(payload: dict[str, Any], key: str) -> datetime:
    value = _str(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedBankingResponse(f"missing or invalid {key}") from exc
    if parsed.tzinfo is None:
        raise MalformedBankingResponse(f"missing timezone in {key}")
    return parsed
