"""StepUpChallenge invariants (BR-5): single-use, TTL, binding, attempt cap."""

import uuid
from datetime import UTC, datetime, timedelta

from identity_access.domain.entities import StepUpChallenge
from shared.domain.result import is_err, is_ok

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
TTL = timedelta(minutes=5)


def make_challenge(code: str = "123456", operation_hash: str = "op-hash-1") -> StepUpChallenge:
    return StepUpChallenge.issue(
        challenge_id=uuid.UUID(int=1),
        user_id=uuid.UUID(int=2),
        operation_hash=operation_hash,
        code=code,
        now=NOW,
        ttl=TTL,
    )


class TestIssue:
    def test_code_never_stored_in_plaintext(self) -> None:
        challenge = make_challenge(code="123456")
        assert "123456" not in challenge.code_hash

    def test_expiry_is_now_plus_ttl(self) -> None:
        assert make_challenge().expires_at == NOW + TTL


class TestVerify:
    def test_correct_code_and_operation_succeeds(self) -> None:
        challenge = make_challenge()
        assert is_ok(challenge.verify(code="123456", operation_hash="op-hash-1", now=NOW))
        assert challenge.used_at == NOW

    def test_single_use_second_submission_fails(self) -> None:
        challenge = make_challenge()
        challenge.verify(code="123456", operation_hash="op-hash-1", now=NOW)
        second = challenge.verify(code="123456", operation_hash="op-hash-1", now=NOW)
        assert is_err(second)
        assert second.error.code == "step_up.already_used"

    def test_expired_challenge_fails(self) -> None:
        challenge = make_challenge()
        result = challenge.verify(code="123456", operation_hash="op-hash-1", now=NOW + TTL)
        assert is_err(result)
        assert result.error.code == "step_up.expired"

    def test_operation_binding_rejects_other_hash(self) -> None:
        """BR-5.3: a code cannot authorize a different operation."""
        challenge = make_challenge()
        result = challenge.verify(code="123456", operation_hash="op-hash-2", now=NOW)
        assert is_err(result)
        assert result.error.code == "step_up.operation_mismatch"

    def test_wrong_code_fails(self) -> None:
        challenge = make_challenge()
        result = challenge.verify(code="654321", operation_hash="op-hash-1", now=NOW)
        assert is_err(result)
        assert result.error.code == "step_up.invalid_code"

    def test_three_wrong_codes_lock_even_the_correct_one(self) -> None:
        challenge = make_challenge()
        for _ in range(3):
            challenge.verify(code="000000", operation_hash="op-hash-1", now=NOW)
        locked = challenge.verify(code="123456", operation_hash="op-hash-1", now=NOW)
        assert is_err(locked)
        assert locked.error.code == "step_up.locked"

    def test_wrong_operation_hash_counts_as_attempt(self) -> None:
        challenge = make_challenge()
        challenge.verify(code="123456", operation_hash="op-hash-2", now=NOW)
        assert challenge.attempts == 1
