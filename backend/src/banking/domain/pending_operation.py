"""Confirmation-bound operation aggregate (BR-6)."""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class OperationStatus(StrEnum):
    PENDING_STEP_UP = "pending_stepup"
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTING = "executing"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class TransitionError(ValueError):
    pass


@dataclass(frozen=True, kw_only=True)
class PendingOperation:
    operation_id: uuid.UUID
    user_id: uuid.UUID
    tool: str
    params: dict[str, Any]
    tier: int
    operation_hash: str
    status: OperationStatus
    created_at: datetime
    expires_at: datetime
    cancellation_reason: str | None = None
    idempotency_key: str | None = None
    resolved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        operation_id: uuid.UUID,
        user_id: uuid.UUID,
        tool: str,
        params: dict[str, Any],
        tier: int,
        now: datetime,
        ttl: timedelta,
    ) -> "PendingOperation":
        return cls(
            operation_id=operation_id,
            user_id=user_id,
            tool=tool,
            params=dict(params),
            tier=tier,
            # An operation hash identifies one confirmation attempt. It must
            # not be deterministic from the business parameters: a user can
            # legitimately submit the same PIX again after an old attempt has
            # expired, been cancelled, or completed.
            operation_hash=str(operation_id),
            status=OperationStatus.PENDING_CONFIRMATION,
            created_at=now,
            expires_at=now + ttl,
            # The key remains stable for retries of this exact attempt and is
            # therefore safe for the MCP write path.
            idempotency_key=str(operation_id),
        )

    def begin_execution(self, *, now: datetime) -> "PendingOperation":
        if self.status is not OperationStatus.PENDING_CONFIRMATION:
            raise TransitionError("operation is not awaiting confirmation")
        if now >= self.expires_at:
            raise TransitionError("operation is expired")
        return replace(self, status=OperationStatus.EXECUTING)

    def complete_step_up(self, *, now: datetime) -> "PendingOperation":
        if self.status is not OperationStatus.PENDING_STEP_UP:
            raise TransitionError("operation is not awaiting step-up")
        if now >= self.expires_at:
            raise TransitionError("operation is expired")
        return replace(self, status=OperationStatus.PENDING_CONFIRMATION)

    def cancel(self, *, reason: str, now: datetime | None = None) -> "PendingOperation":
        if self.status not in (
            OperationStatus.PENDING_STEP_UP,
            OperationStatus.PENDING_CONFIRMATION,
        ):
            raise TransitionError("only a pending operation can be cancelled")
        return replace(
            self, status=OperationStatus.CANCELLED, cancellation_reason=reason, resolved_at=now
        )

    def expire(self, *, now: datetime) -> "PendingOperation":
        if (
            self.status
            not in (OperationStatus.PENDING_STEP_UP, OperationStatus.PENDING_CONFIRMATION)
            or now < self.expires_at
        ):
            raise TransitionError("operation cannot expire")
        return replace(self, status=OperationStatus.EXPIRED, resolved_at=now)

    def complete(self, *, now: datetime | None = None) -> "PendingOperation":
        if self.status is not OperationStatus.EXECUTING:
            raise TransitionError("only an executing operation can complete")
        return replace(self, status=OperationStatus.EXECUTED, resolved_at=now)

    def fail(self, *, now: datetime | None = None) -> "PendingOperation":
        if self.status is not OperationStatus.EXECUTING:
            raise TransitionError("only an executing operation can fail")
        return replace(self, status=OperationStatus.FAILED, resolved_at=now)
