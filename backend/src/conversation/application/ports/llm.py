"""LlmPort: the only way the agent talks to a model (llm-providers.md §1).

Provider-agnostic by contract (ADR-008): system/user/assistant roles only, JSON
schema for structured output, tolerant parsing upstream. Every return carries
`model` and `provider` so telemetry can show which provider actually served the
turn — drift between providers must be visible, not inferred.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, kw_only=True)
class LlmMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True, kw_only=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, kw_only=True)
class Completion:
    text: str
    provider: str
    model: str
    usage: Usage = Usage()


@dataclass(frozen=True, kw_only=True)
class Delta:
    """One streamed fragment. `provider`/`model` repeat on every delta so the
    AG-UI layer can tag a stream that failed over mid-flight."""

    text: str
    provider: str
    model: str


class LlmError(Exception):
    """Provider failure. `retriable` drives the fallback matrix (429/5xx yes,
    4xx no — a bad request is our bug and must surface, not rotate providers)."""

    def __init__(self, message: str, *, provider: str, retriable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.retriable = retriable


class LlmPort(Protocol):
    """`json_schema` requests structured output; adapters that cannot enforce it
    natively still pass it as an instruction — parsing stays tolerant either way."""

    @property
    def provider(self) -> str: ...

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion: ...

    def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]: ...
