"""Typed identity failures. Login errors never reveal whether the user exists."""

from shared.domain.errors import DomainError


def invalid_credentials() -> DomainError:
    """Single error for unknown user AND wrong password (no-leak, PRD-004)."""
    return DomainError(code="auth.invalid_credentials", message="Invalid credentials")


def token_invalid() -> DomainError:
    return DomainError(code="auth.token_invalid", message="Token signature or shape invalid")


def token_expired() -> DomainError:
    return DomainError(code="auth.token_expired", message="Token expired")


def step_up_challenge_not_found() -> DomainError:
    """Also returned for challenges owned by another user (no existence leak)."""
    return DomainError(code="step_up.challenge_not_found", message="Challenge not found")


def step_up_already_used() -> DomainError:
    return DomainError(code="step_up.already_used", message="Challenge already consumed (BR-5.2)")


def step_up_locked() -> DomainError:
    return DomainError(code="step_up.locked", message="Attempt limit reached; challenge locked")


def step_up_expired() -> DomainError:
    return DomainError(code="step_up.expired", message="Challenge expired (BR-5.2)")


def step_up_operation_mismatch() -> DomainError:
    return DomainError(
        code="step_up.operation_mismatch",
        message="Challenge is bound to a different operation (BR-5.3)",
    )


def step_up_invalid_code() -> DomainError:
    return DomainError(code="step_up.invalid_code", message="Wrong code")


def step_up_missing_operation() -> DomainError:
    return DomainError(
        code="step_up.missing_operation", message="operation_hash is required to issue a challenge"
    )
