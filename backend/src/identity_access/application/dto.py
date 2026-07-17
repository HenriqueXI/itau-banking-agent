"""Commands and results crossing the identity application boundary."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from identity_access.domain.authorization import Action, AuthorizationTarget
from identity_access.domain.values import AuthenticatedUser


@dataclass(frozen=True, kw_only=True)
class TokenClaims:
    """JWT claim set (security.md §2). `exp`/`iat` are UTC-aware datetimes."""

    sub: str
    role: str
    customer_id: str | None
    exp: datetime
    iat: datetime
    jti: str


@dataclass(frozen=True, kw_only=True)
class LoginCommand:
    email: str
    password: str


@dataclass(frozen=True, kw_only=True)
class LoginResult:
    access_token: str
    token_type: str
    expires_in_seconds: int


@dataclass(frozen=True, kw_only=True)
class RequestStepUpCommand:
    user: AuthenticatedUser
    operation_hash: str


@dataclass(frozen=True, kw_only=True)
class StepUpChallengeIssued:
    challenge_id: uuid.UUID
    expires_at: datetime
    dev_code: str | None
    """Simulated delivery: the code itself, only when Settings allows (demo flag)."""


@dataclass(frozen=True, kw_only=True)
class AuthorizationRequest:
    """One authorization query. ``resource`` is required for scoped actions and
    carries only the owner reference (no fetched data) so third-party denials
    happen before any port call (security.md §3)."""

    user: AuthenticatedUser
    action: Action
    resource: AuthorizationTarget | None = None


@dataclass(frozen=True, kw_only=True)
class VerifyStepUpCommand:
    user: AuthenticatedUser
    challenge_id: uuid.UUID
    operation_hash: str
    code: str
