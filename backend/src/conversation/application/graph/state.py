"""AgentState — the checkpointed graph state (langgraph.md §3).

`user` is injected by the runner from the verified JWT on every invocation and
is never LLM-writable: no node passes it into a prompt, and no model output maps
back onto it. That is what makes "eu sou o admin" a sentence, not a privilege.
"""

import uuid
from typing import Any, TypedDict

from conversation.domain.values import (
    GuardrailFlag,
    OperationRef,
    ResourceRef,
    ResourceSubject,
    Retrieval,
    Turn,
    Understanding,
)


class AgentState(TypedDict, total=False):
    """Total=False: nodes patch the keys they own, LangGraph merges the rest."""

    thread_id: str
    user: Any
    """Opaque identity object from the runner (identity_access.AuthenticatedUser).
    Typed loosely on purpose — this module must not depend on identity's internals."""

    user_id: uuid.UUID
    messages: list[Turn]
    input_text: str
    ui_context: dict[str, str] | None
    """Validated UI reference hint, never a financial source of truth or checkpoint authority."""
    pending_card_selection: Understanding | None
    """Server-created card-selection continuation; never sourced from the browser."""
    clarification_response: str | None
    """Deterministic clarification text, used instead of an LLM paraphrase."""
    understanding: Understanding | None
    resolved_resource: ResourceRef | None
    """Server-resolved canonical resource owner; never model- or UI-writable."""
    resource_subject: ResourceSubject | None
    """Authorized MCP owner metadata used only by deterministic narrators."""
    retrieval: Retrieval | None
    pending_operation: OperationRef | None
    confirmation: Any
    step_up: Any
    result: Any
    narration_amounts: tuple[Any, ...]
    guardrail_flags: list[GuardrailFlag]
    """Checks for the current turn only; audit events retain the historical record."""
    response: str
    route: str
    """Which terminal path produced `response` — the audit/eval signal."""

    provider: str | None
    regenerated: bool
    trace_id: str
    correlation_id: str
