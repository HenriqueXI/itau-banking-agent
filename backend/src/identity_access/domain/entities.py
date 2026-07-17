"""Identity aggregates: User and StepUpChallenge (BR-1, BR-5)."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from identity_access.domain.errors import (
    step_up_already_used,
    step_up_expired,
    step_up_invalid_code,
    step_up_locked,
    step_up_operation_mismatch,
)
from identity_access.domain.values import Role
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result


@dataclass(frozen=True, kw_only=True)
class User:
    """Seeded demo persona. Password hash is opaque here (argon2 in adapters)."""

    id: uuid.UUID
    email: str
    name: str
    role: Role
    customer_id: str | None
    password_hash: str
    created_at: datetime


MAX_ATTEMPTS = 3


def _hash_code(challenge_id: uuid.UUID, code: str) -> str:
    """Salted SHA-256 — 6-digit codes never rest in plaintext (NFR-1)."""
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


@dataclass(kw_only=True)
class StepUpChallenge:
    """Single-use second factor bound to one operation (BR-5).

    Invariants enforced by `verify`: single-use, TTL, operation binding,
    attempt cap. Every submission counts as an attempt; once locked, the
    challenge fails regardless of code correctness.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    operation_hash: str
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    used_at: datetime | None = field(default=None)

    @classmethod
    def issue(
        cls,
        *,
        challenge_id: uuid.UUID,
        user_id: uuid.UUID,
        operation_hash: str,
        code: str,
        now: datetime,
        ttl: timedelta,
    ) -> "StepUpChallenge":
        return cls(
            id=challenge_id,
            user_id=user_id,
            operation_hash=operation_hash,
            code_hash=_hash_code(challenge_id, code),
            expires_at=now + ttl,
        )

    def verify(self, *, code: str, operation_hash: str, now: datetime) -> Result[None, DomainError]:
        if self.used_at is not None:
            return Err(step_up_already_used())
        if self.attempts >= MAX_ATTEMPTS:
            return Err(step_up_locked())
        if now >= self.expires_at:
            return Err(step_up_expired())

        self.attempts += 1
        code_matches = hmac.compare_digest(_hash_code(self.id, code), self.code_hash)
        operation_matches = hmac.compare_digest(operation_hash, self.operation_hash)
        if not operation_matches:
            return Err(step_up_operation_mismatch())
        if not code_matches:
            if self.attempts >= MAX_ATTEMPTS:
                return Err(step_up_locked())
            return Err(step_up_invalid_code())

        self.used_at = now
        return Ok(None)
