"""Conversation domain events (`conversation.<PastTenseFact>`).

Payloads carry masked, non-reversible detail only: a guardrail event says which
check fired, never the offending text (data-flow.md §6 — an injection payload in
an audit row is the injection, stored).
"""

from dataclasses import dataclass
from typing import ClassVar

from shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class GuardrailTriggered(DomainEvent):
    """A guardrail blocked or sanitized a turn (guardrails.md §1, audited)."""

    event_type: ClassVar[str] = "conversation.GuardrailTriggered"

    thread_id: str
    check_id: str
    disposition: str
    ring: str
    """"input" | "output" — which ring fired (security.md rings 2 and 4)."""


@dataclass(frozen=True, kw_only=True)
class ConversationTurnCompleted(DomainEvent):
    """A turn finished. The audit trail's conversational spine; carries the
    routing decision, not the content."""

    event_type: ClassVar[str] = "conversation.ConversationTurnCompleted"

    thread_id: str
    intent: str
    route: str
    citation_count: int
    provider: str | None
