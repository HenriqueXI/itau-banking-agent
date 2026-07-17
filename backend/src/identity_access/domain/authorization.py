"""Deterministic authorization: the single decision point (FR-4, ADR-011).

The LLM never authorizes — this table plus pure functions do. Deliberately
boring: a data-driven matrix (security.md §3) and side-effect-free evaluation.
The exhaustive test in ``test_authorization_matrix.py`` IS the spec; reviewers
read it against security.md §3.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from identity_access.domain.values import AuthenticatedUser, Role

logger = logging.getLogger(__name__)


class Action(StrEnum):
    """Every authorizable action. Registered tools map to one of these (BR-4.1)."""

    VIEW_PROFILE = "view_profile"
    VIEW_LIMIT = "view_limit"
    VIEW_BALANCE = "view_balance"
    VIEW_INVOICE = "view_invoice"
    VIEW_TRANSACTIONS = "view_transactions"
    UPDATE_CARD_LIMIT = "update_card_limit"
    CREATE_PIX = "create_pix"
    KB_QUERY = "kb_query"
    VIEW_AUDIT = "view_audit"


class DenyReason(StrEnum):
    """User-safe denial category. Presentation maps it to pt-BR refusal copy; a
    denial never confirms the target resource exists (FR-5, UC-3)."""

    ROLE_FORBIDDEN = "role_forbidden"
    MALFORMED = "malformed"
    INTERNAL_ERROR = "internal_error"


@runtime_checkable
class AuthorizationTarget(Protocol):
    """Minimal shape the decision needs: who owns the resource. Callers pass a
    lightweight reference (the target owner id) BEFORE fetching data, so
    third-party denials happen pre-fetch (security.md §3)."""

    @property
    def owner_id(self) -> str | None: ...


@dataclass(frozen=True)
class Permit:
    pass


@dataclass(frozen=True)
class Deny:
    reason: DenyReason


type Decision = Permit | Deny


def is_permitted(decision: Decision) -> bool:
    return isinstance(decision, Permit)


_ALL_ROLES = frozenset(Role)
_PRIVILEGED = frozenset({Role.MANAGER, Role.ADMIN})


@dataclass(frozen=True)
class _Rule:
    """One matrix row. ``scoped`` = ownership matters. For scoped actions,
    ``owner_roles`` may act on resources the caller owns and ``third_party_roles``
    on resources they do not; for unscoped actions only ``owner_roles`` applies.
    A privileged role never owns a resource (no ``customer_id``), so it always
    resolves through ``third_party_roles`` — that is intended."""

    scoped: bool
    owner_roles: frozenset[Role]
    third_party_roles: frozenset[Role] = frozenset()


# security.md §3 — verbatim. Changing this table changes the spec test.
_VIEW = _Rule(scoped=True, owner_roles=_ALL_ROLES, third_party_roles=_PRIVILEGED)

MATRIX: dict[Action, _Rule] = {
    Action.VIEW_PROFILE: _VIEW,
    Action.VIEW_LIMIT: _VIEW,
    Action.VIEW_BALANCE: _VIEW,
    Action.VIEW_INVOICE: _VIEW,
    Action.VIEW_TRANSACTIONS: _VIEW,
    # Challenge RBAC matrix: changing a card limit is reserved for privileged
    # staff. A customer is denied even when the card is their own (BR-2.5).
    # Eligibility (BR-2.2/2.3 max limit) is business-rule validation downstream,
    # not an authorization concern.
    Action.UPDATE_CARD_LIMIT: _Rule(
        scoped=True, owner_roles=_PRIVILEGED, third_party_roles=_PRIVILEGED
    ),
    # Own account for every role (BR-3.5) — no third-party path for anyone.
    Action.CREATE_PIX: _Rule(scoped=True, owner_roles=_ALL_ROLES, third_party_roles=frozenset()),
    Action.KB_QUERY: _Rule(scoped=False, owner_roles=_ALL_ROLES),
    Action.VIEW_AUDIT: _Rule(scoped=False, owner_roles=frozenset({Role.ADMIN})),
}


class AuthorizationService:
    """Single deterministic decision point. Pure and synchronous — zero I/O in
    the decision path (NFR-7). Fail-closed: any unexpected condition is Deny."""

    def authorize(
        self,
        user: AuthenticatedUser,
        action: Action,
        resource: AuthorizationTarget | None = None,
    ) -> Decision:
        try:
            return self._decide(user, action, resource)
        except Exception:  # fail-closed (FR-3): never propagate to the caller
            logger.exception("authorization.internal_error action=%s", action)
            return Deny(DenyReason.INTERNAL_ERROR)

    def _decide(
        self,
        user: AuthenticatedUser,
        action: Action,
        resource: AuthorizationTarget | None,
    ) -> Decision:
        if not isinstance(user.role, Role):  # corrupt token edge
            logger.warning("authorization.corrupt_role value=%r", user.role)
            return Deny(DenyReason.INTERNAL_ERROR)

        rule = MATRIX.get(action)
        if rule is None:  # unreachable once assert_matrix_complete runs at boot
            logger.error("authorization.unregistered_action action=%s", action)
            return Deny(DenyReason.INTERNAL_ERROR)

        if not rule.scoped:
            return _permit_if(user.role in rule.owner_roles)

        if resource is None or resource.owner_id is None:
            return Deny(DenyReason.MALFORMED)

        owned = resource.owner_id == user.customer_id
        allowed = rule.owner_roles if owned else rule.third_party_roles
        return _permit_if(user.role in allowed)


def _permit_if(condition: bool) -> Decision:
    return Permit() if condition else Deny(DenyReason.ROLE_FORBIDDEN)


def assert_matrix_complete() -> None:
    """Startup guard (FR-6): every Action has a matrix row. A gap is a boot
    failure, not a runtime Permit/Deny guess."""
    missing = [action for action in Action if action not in MATRIX]
    if missing:
        raise RuntimeError(f"Authorization matrix incomplete — missing rows for {missing}")
