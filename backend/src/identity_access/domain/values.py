"""Identity value objects (BR-1)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Exactly one role per session (BR-1.1)."""

    CUSTOMER = "customer"
    MANAGER = "manager"
    ADMIN = "admin"


@dataclass(frozen=True, kw_only=True)
class AuthenticatedUser:
    """Verified identity built from JWT claims only (BR-1.2).

    This is the object every authorization decision receives — nothing in
    conversation content can produce or mutate one.
    """

    id: uuid.UUID
    role: Role
    customer_id: str | None = None

    def __post_init__(self) -> None:
        if self.role is Role.CUSTOMER and not self.customer_id:
            raise ValueError("customer role requires a customer_id (BR-1.3)")
