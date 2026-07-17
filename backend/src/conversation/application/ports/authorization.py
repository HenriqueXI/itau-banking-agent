"""AuthorizationPort: the graph's view of the single decision point (ADR-011).

The `authorize` node calls this and nothing else — no local rules, no LLM,
no "the model said the user owns it". The bridge in the composition root wires
it to `identity_access.AuthorizeAction`, which is where the decision (and the
deny event) actually happens.

`user` is typed as `object` here on purpose: the identity object is JWT-derived,
LLM-opaque, and this module has no business inspecting it — it carries it from
the runner to the decision point unchanged (langgraph.md §3).
"""

from dataclasses import dataclass
from typing import Protocol

from conversation.domain.values import ResourceRef


@dataclass(frozen=True, kw_only=True)
class AuthorizationOutcome:
    """Permit, or deny with a user-safe reason category. A denial never
    confirms the resource exists (UC-3)."""

    permitted: bool
    reason: str | None = None


class AuthorizationPort(Protocol):
    async def authorize(
        self, *, user: object, action: str, resource: ResourceRef | None = None
    ) -> AuthorizationOutcome: ...
