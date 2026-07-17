"""Conversation value objects: what a turn is made of (langgraph.md §3).

These types are the module's own vocabulary. `Citation` and `Evidence` mirror
knowledge's shapes deliberately — modules never import each other's internals
(backend/README rule 3); the composition root translates at the port boundary.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Intent(StrEnum):
    """What the user is asking for, as classified by `understand`.

    One entry per agent-facing capability plus the two
    conversational outcomes that reach no capability at all.
    """

    KB_QUERY = "kb_query"
    VIEW_PROFILE = "view_profile"
    VIEW_LIMIT = "view_limit"
    VIEW_BALANCE = "view_balance"
    VIEW_INVOICE = "view_invoice"
    VIEW_TRANSACTIONS = "view_transactions"
    HYBRID_INVOICE_GUIDANCE = "hybrid_invoice_guidance"
    UPDATE_CARD_LIMIT = "update_card_limit"
    CREATE_PIX = "create_pix"
    SMALLTALK = "smalltalk"
    UNCLEAR = "unclear"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, kw_only=True)
class Turn:
    """One conversation message. The history window is a list of these."""

    role: Role
    content: str
    citations: tuple["Citation", ...] = ()
    """Sources rendered alongside this assistant turn, when it is RAG-backed."""


@dataclass(frozen=True, kw_only=True)
class ResourceRef:
    """Owner reference for an ownership check, built BEFORE any data fetch.

    Structurally satisfies identity_access's `AuthorizationTarget` (owner_id),
    which is why the authorization bridge needs no mapping of its own.
    """

    kind: str
    owner_id: str | None
    id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ResourceSubject:
    """Server-derived owner metadata used only to render a banking result.

    It is created from the authenticated user plus an authorized MCP profile;
    neither the LLM nor the browser can write it.
    """

    customer_id: str
    name: str | None
    is_self: bool


@dataclass(frozen=True, kw_only=True)
class Citation:
    """Structured citation; rendered as `【title — section/page】` (rag.md §4)."""

    document_id: str
    title: str
    section: str
    page: int | None = None

    def marker(self) -> str:
        locus = f"p.{self.page}" if self.page is not None else self.section
        return f"【{self.title} — {locus}】"


@dataclass(frozen=True, kw_only=True)
class Evidence:
    """A retrieved chunk as the generation prompt sees it: delimited data,
    never instructions (rag.md §7)."""

    text: str
    citation: Citation
    score: float


@dataclass(frozen=True, kw_only=True)
class Retrieval:
    """Result of the `retrieve` node — grounded evidence or the refusal path."""

    query: str
    evidence: tuple[Evidence, ...]
    below_floor: bool
    best_score: float | None

    @property
    def citations(self) -> tuple[Citation, ...]:
        return tuple(e.citation for e in self.evidence)


@dataclass(frozen=True, kw_only=True)
class Understanding:
    """`understand` output (langgraph.md §2): the only thing the LLM decides.

    It classifies and extracts; it never authorizes. `missing_param` names the
    single most blocking gap so `clarify` asks exactly one question (FR-1.4).
    """

    intent: Intent
    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    target_resource: ResourceRef | None = None
    references_resolved: bool = False
    missing_param: str | None = None
    ambiguity: str | None = None

    @property
    def needs_clarification(self) -> bool:
        return (
            self.intent is Intent.UNCLEAR
            or self.missing_param is not None
            or self.ambiguity is not None
        )


class Disposition(StrEnum):
    """What a guardrail check decided (guardrails.md §1)."""

    PASS = "pass"
    FLAG = "flag"
    SANITIZE = "sanitize"
    BLOCK = "block"


@dataclass(frozen=True, kw_only=True)
class GuardrailFlag:
    """One triggered check. `detail` is internal-only — user-facing copy never
    says which pattern fired (guardrails.md §4: don't teach the attacker)."""

    check_id: str
    disposition: Disposition
    detail: str

    @property
    def blocking(self) -> bool:
        return self.disposition is Disposition.BLOCK


@dataclass(frozen=True, kw_only=True)
class OperationRef:
    """Pointer to a pending operation (hash → pending_operations table).

    PRD-006 only carries it through state; PRD-007 gives it effects.
    """

    operation_hash: str
    tool: str
    tier: int


@dataclass(frozen=True, kw_only=True)
class ConversationThread:
    """Thread ownership binding: a thread belongs to exactly one user, forever.

    Checked on every run/resume — a user can never attach to another's thread
    (api.md, PRD006-FR-6).
    """

    thread_id: str
    user_id: uuid.UUID

    def belongs_to(self, user_id: uuid.UUID) -> bool:
        return self.user_id == user_id
