"""The exhaustive matrix test IS the spec (PRD-005). Every cell is a hardcoded
literal read against security.md §3 — no logic that could drift with the code.
"""

import uuid
from dataclasses import dataclass

import pytest

from identity_access.domain.authorization import (
    MATRIX,
    Action,
    AuthorizationService,
    Deny,
    DenyReason,
    Permit,
    assert_matrix_complete,
    is_permitted,
)
from identity_access.domain.values import AuthenticatedUser, Role


@dataclass(frozen=True)
class Target:
    owner_id: str | None


CUSTOMER = AuthenticatedUser(id=uuid.UUID(int=1), role=Role.CUSTOMER, customer_id="cust-1")
MANAGER = AuthenticatedUser(id=uuid.UUID(int=2), role=Role.MANAGER)
ADMIN = AuthenticatedUser(id=uuid.UUID(int=3), role=Role.ADMIN)

OWN = Target(owner_id="cust-1")  # owned by CUSTOMER
OTHER = Target(owner_id="cust-2")  # third party

VIEW_ACTIONS = (Action.VIEW_PROFILE, Action.VIEW_LIMIT, Action.VIEW_BALANCE)


@pytest.fixture
def service() -> AuthorizationService:
    return AuthorizationService()


# (user, action, resource, permit?) — every role/action/ownership cell.
# Manager/admin never own a resource (no customer_id) → always the third-party branch.
def _view_cells() -> list[tuple]:
    cells: list[tuple] = []
    for action in VIEW_ACTIONS:
        cells += [
            (CUSTOMER, action, OWN, True),
            (CUSTOMER, action, OTHER, False),
            (MANAGER, action, OWN, True),
            (MANAGER, action, OTHER, True),
            (ADMIN, action, OWN, True),
            (ADMIN, action, OTHER, True),
        ]
    return cells


MATRIX_CELLS: list[tuple] = [
    *_view_cells(),
    # update_card_limit — challenge matrix: privileged staff only (BR-2.5).
    (CUSTOMER, Action.UPDATE_CARD_LIMIT, OWN, False),
    (CUSTOMER, Action.UPDATE_CARD_LIMIT, OTHER, False),
    (MANAGER, Action.UPDATE_CARD_LIMIT, OWN, True),
    (MANAGER, Action.UPDATE_CARD_LIMIT, OTHER, True),
    (ADMIN, Action.UPDATE_CARD_LIMIT, OWN, True),
    (ADMIN, Action.UPDATE_CARD_LIMIT, OTHER, True),
    # create_pix — own account only, all roles (BR-3.5); privileged have no own account.
    (CUSTOMER, Action.CREATE_PIX, OWN, True),
    (CUSTOMER, Action.CREATE_PIX, OTHER, False),
    (MANAGER, Action.CREATE_PIX, OWN, False),
    (MANAGER, Action.CREATE_PIX, OTHER, False),
    (ADMIN, Action.CREATE_PIX, OWN, False),
    (ADMIN, Action.CREATE_PIX, OTHER, False),
    # kb_query — everyone, no ownership.
    (CUSTOMER, Action.KB_QUERY, None, True),
    (MANAGER, Action.KB_QUERY, None, True),
    (ADMIN, Action.KB_QUERY, None, True),
    # view_audit — admin only.
    (CUSTOMER, Action.VIEW_AUDIT, None, False),
    (MANAGER, Action.VIEW_AUDIT, None, False),
    (ADMIN, Action.VIEW_AUDIT, None, True),
]


@pytest.mark.parametrize(("user", "action", "resource", "permit"), MATRIX_CELLS)
def test_authorization_matrix(
    service: AuthorizationService,
    user: AuthenticatedUser,
    action: Action,
    resource: Target | None,
    permit: bool,
) -> None:
    decision = service.authorize(user, action, resource)
    assert is_permitted(decision) is permit


def test_customer_third_party_view_denies_role_forbidden(service: AuthorizationService) -> None:
    # Denial category never confirms the resource exists (FR-5, UC-3).
    decision = service.authorize(CUSTOMER, Action.VIEW_BALANCE, OTHER)
    assert decision == Deny(DenyReason.ROLE_FORBIDDEN)


def test_manager_on_own_resource_permits_via_role(service: AuthorizationService) -> None:
    # Edge case: privileged role permitted through the role branch, not ownership.
    assert isinstance(service.authorize(MANAGER, Action.VIEW_PROFILE, OWN), Permit)


@pytest.mark.parametrize(
    "action",
    [
        Action.VIEW_PROFILE,
        Action.VIEW_LIMIT,
        Action.VIEW_BALANCE,
        Action.UPDATE_CARD_LIMIT,
        Action.CREATE_PIX,
    ],
)
def test_scoped_action_without_resource_is_malformed(
    service: AuthorizationService, action: Action
) -> None:
    assert service.authorize(CUSTOMER, action, None) == Deny(DenyReason.MALFORMED)


def test_scoped_action_with_ownerless_resource_is_malformed(
    service: AuthorizationService,
) -> None:
    assert service.authorize(CUSTOMER, Action.VIEW_BALANCE, Target(owner_id=None)) == Deny(
        DenyReason.MALFORMED
    )


def test_fail_closed_on_unexpected_exception(service: AuthorizationService) -> None:
    class Exploding:
        @property
        def owner_id(self) -> str | None:
            raise RuntimeError("sabotaged lookup")

    decision = service.authorize(CUSTOMER, Action.VIEW_BALANCE, Exploding())
    assert decision == Deny(DenyReason.INTERNAL_ERROR)


def test_corrupt_role_denies_internal_error(service: AuthorizationService) -> None:
    class CorruptUser:
        def __init__(self) -> None:
            self.id = uuid.UUID(int=9)
            self.role = "superuser"  # outside the Role enum (corrupt token edge)
            self.customer_id: str | None = None

    decision = service.authorize(CorruptUser(), Action.KB_QUERY, None)  # type: ignore[arg-type]
    assert decision == Deny(DenyReason.INTERNAL_ERROR)


def test_matrix_covers_every_action() -> None:
    assert set(MATRIX) == set(Action)
    assert_matrix_complete()  # does not raise


def test_missing_action_is_a_boot_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    trimmed = {a: r for a, r in MATRIX.items() if a is not Action.VIEW_AUDIT}
    monkeypatch.setattr("identity_access.domain.authorization.MATRIX", trimmed)
    with pytest.raises(RuntimeError, match="matrix incomplete"):
        assert_matrix_complete()
