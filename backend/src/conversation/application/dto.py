"""Commands and stream events crossing the conversation application boundary.

The stream events are protocol-agnostic on purpose: AG-UI is one encoding of
them (api.md), and the eval harness consumes the same objects without an HTTP
server in the way.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from conversation.domain.values import Citation


@dataclass(frozen=True, kw_only=True)
class RunTurnCommand:
    """One user turn. `user` is the verified identity object from the JWT — the
    runner injects it into state per invocation and nothing else may set it."""

    user: Any
    user_id: uuid.UUID
    thread_id: str
    message: str
    ui_context: dict[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class ResumeTurnCommand:
    """A client's answer to an interrupt (confirm / cancel / step-up code).

    The payload is a claim, not a command: server state decides what it means
    (api.md). Carried here so the transport contract is stable before the gates
    that produce interrupts exist (PRD-007).
    """

    user: Any
    user_id: uuid.UUID
    thread_id: str
    operation_hash: str
    response: str
    stage: str = "confirmation"
    challenge_id: uuid.UUID | None = None


@dataclass(frozen=True, kw_only=True)
class AgentEvent:
    """Base for everything the runner streams."""


@dataclass(frozen=True, kw_only=True)
class RunStarted(AgentEvent):
    thread_id: str
    run_id: str


@dataclass(frozen=True, kw_only=True)
class ToolCallStarted(AgentEvent):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ToolCallEnded(AgentEvent):
    tool_call_id: str
    tool_name: str
    result_summary: str


@dataclass(frozen=True, kw_only=True)
class TextDelta(AgentEvent):
    message_id: str
    delta: str


@dataclass(frozen=True, kw_only=True)
class ConfirmationRequired(AgentEvent):
    """Server-owned confirmation card data; the UI must never reconstruct it."""

    operation_hash: str
    operation: str
    current_amount: str
    requested_amount: str
    expires_at: str
    issued_at: str
    recipient_key_masked: str | None = None
    account_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class StepUpRequired(AgentEvent):
    operation_hash: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class CitationsEmitted(AgentEvent):
    citations: tuple[Citation, ...]


@dataclass(frozen=True, kw_only=True)
class StateSnapshot(AgentEvent):
    """Read-only hints for the UI (ag-ui.md). Never carries identity or evidence."""

    route: str
    intent: str | None
    pending_operation_hash: str | None
    data_changed: bool = False


@dataclass(frozen=True, kw_only=True)
class RunFinished(AgentEvent):
    thread_id: str
    run_id: str
    route: str


@dataclass(frozen=True, kw_only=True)
class RunError(AgentEvent):
    message: str
    correlation_id: str
