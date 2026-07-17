# ruff: noqa: N818
"""Typed failures emitted by the banking boundary.

The rest of the application never needs to understand MCP error payloads.
"""


class BankingSystemError(Exception):
    """Base class for deterministic banking-system failures."""


class CustomerNotFound(BankingSystemError):
    pass


class CardNotFound(BankingSystemError):
    pass


class AccountNotFound(BankingSystemError):
    pass


class InvalidBankingRequest(BankingSystemError):
    pass


class InsufficientFunds(BankingSystemError):
    pass


class SystemUnavailable(BankingSystemError):
    pass


class MalformedBankingResponse(BankingSystemError):
    pass
