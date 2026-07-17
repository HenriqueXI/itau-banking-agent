"""Identity domain events (PRD004-FR-6). Outbox delivery lands in PRD-014."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class AuthorizationDenied(DomainEvent):
    """Raised on every authorization refusal (FR-4, audited via FR-6.3).

    Payload carries an action label, an owner reference (never third-party PII),
    and a user-safe reason category — enough for audit without confirming the
    resource exists (UC-3)."""

    event_type: ClassVar[str] = "identity.AuthorizationDenied"

    attempted_action: str
    target_resource: str | None
    reason: str


@dataclass(frozen=True, kw_only=True)
class StepUpIssued(DomainEvent):
    event_type: ClassVar[str] = "identity.StepUpIssued"

    challenge_id: uuid.UUID
    operation_hash: str
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class StepUpSucceeded(DomainEvent):
    event_type: ClassVar[str] = "identity.StepUpSucceeded"

    challenge_id: uuid.UUID
    operation_hash: str


@dataclass(frozen=True, kw_only=True)
class StepUpFailed(DomainEvent):
    event_type: ClassVar[str] = "identity.StepUpFailed"

    challenge_id: uuid.UUID
    operation_hash: str
    reason: str
