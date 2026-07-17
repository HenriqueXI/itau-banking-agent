"""Framework-free values carried from the durable outbox to event handlers."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, kw_only=True)
class StoredEvent:
    """An immutable domain-event envelope read from the outbox."""

    event_id: uuid.UUID
    event_type: str
    version: int
    occurred_at: datetime
    actor_user_id: str | None
    trace_id: str | None
    payload: dict[str, Any]
    attempts: int = 0


@dataclass(frozen=True, kw_only=True)
class OutboxStats:
    pending_count: int
    failed_count: int
    oldest_pending_at: datetime | None
