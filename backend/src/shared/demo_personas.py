"""Stable demo identities consumed by PostgreSQL seed and MCP simulator."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class DemoPersona:
    user_id: uuid.UUID
    email: str
    name: str
    role: str
    customer_id: str | None


DEMO_PERSONAS = (
    DemoPersona(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000a1a"),
        email="ana@demo",
        name="Ana",
        role="customer",
        customer_id="123",
    ),
    DemoPersona(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000b2b"),
        email="bruno@demo",
        name="Bruno",
        role="manager",
        customer_id="456",
    ),
    DemoPersona(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000c3c"),
        email="carla@demo",
        name="Carla",
        role="admin",
        customer_id="789",
    ),
)
