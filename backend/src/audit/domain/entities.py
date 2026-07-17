"""Immutable audit domain records (BR-7)."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from shared.logging.masking import mask_mapping


@dataclass(frozen=True, kw_only=True)
class AuditEvent:
    """Insert-only record derived from one durable domain event."""

    id: uuid.UUID
    event_id: uuid.UUID
    user_ref: str
    action: str
    amount: Decimal | None
    occurred_at: datetime
    resource: str
    outcome: str
    trace_id: str | None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A frozen dataclass alone does not make a supplied dict immutable.
        object.__setattr__(self, "details", MappingProxyType(mask_mapping(dict(self.details))))
