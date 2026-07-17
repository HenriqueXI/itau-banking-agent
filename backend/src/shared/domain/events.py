"""Base class for domain events (naming: `<module>.<PastTenseFact>`)."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, cast

from shared.logging.masking import mask_mapping
from shared.telemetry.correlation import current_trace_id


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Immutable fact that something happened in the domain.

    Subclasses set `event_type` and add their own fields. Payloads must carry
    masked PII only — producers mask at construction (data-flow.md §6).
    `event_id` is the consumer dedupe key; `occurred_at` is UTC-aware.

    `trace_id` stamps itself from the turn's contextvar so an audit row always
    opens the trace that produced it (FR-7.2). Defaulted rather than required
    because events also come from paths that aren't turns (scripts, migrations),
    and there the honest answer is None — not a fabricated id.
    """

    event_type: ClassVar[str] = ""

    event_id: uuid.UUID
    occurred_at: datetime
    actor_user_id: str | None = None
    version: int = 1
    trace_id: str | None = field(default_factory=current_trace_id)

    def payload(self) -> dict[str, Any]:
        """Serializable event body for the outbox (excludes envelope fields)."""
        raw_payload = {
            field_name: value
            for field_name, value in self.__dict__.items()
            if field_name not in ("event_id", "occurred_at", "actor_user_id", "trace_id", "version")
        }
        return cast(dict[str, Any], _json_safe(mask_mapping(raw_payload)))

    def envelope(self) -> dict[str, Any]:
        """Stable, fully serializable transport representation for the outbox."""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "version": self.version,
            "occurred_at": self.occurred_at.isoformat(),
            "actor_user_id": self.actor_user_id,
            "trace_id": self.trace_id,
            "payload": self.payload(),
        }


def _json_safe(value: Any) -> Any:
    """Keep adapter serialization boring: JSONB gets only JSON primitives."""
    if isinstance(value, (uuid.UUID, datetime, Decimal)):
        return str(value) if not isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
