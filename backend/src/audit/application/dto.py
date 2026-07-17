"""Audit application boundary DTOs."""

from dataclasses import dataclass
from datetime import datetime

from audit.domain.entities import AuditEvent


@dataclass(frozen=True, kw_only=True)
class AuditQuery:
    user_ref: str | None = None
    user_refs: tuple[str, ...] | None = None
    action: str | None = None
    from_at: datetime | None = None
    to_at: datetime | None = None
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True, kw_only=True)
class AuditPage:
    items: tuple[AuditEvent, ...]
    total: int
