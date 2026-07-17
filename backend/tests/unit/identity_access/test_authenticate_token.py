"""Bearer token → AuthenticatedUser (BR-1.2: role from JWT only)."""

import uuid
from datetime import UTC, datetime, timedelta

from tests.fakes.providers import FixedClock

from identity_access.adapters.outbound.security.jwt_codec import JwtCodec
from identity_access.application.dto import TokenClaims
from identity_access.application.use_cases.authenticate_token import AuthenticateToken
from identity_access.domain.values import Role
from shared.domain.result import is_err, is_ok

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
SECRET = "unit-test-secret-0123456789abcdef0123456789abcdef"


def make_token(role: str = "customer", customer_id: str | None = "123", sub: str | None = None):
    codec = JwtCodec(secret=SECRET, clock=FixedClock(NOW))
    return codec.encode(
        TokenClaims(
            sub=sub or str(uuid.UUID(int=10)),
            role=role,
            customer_id=customer_id,
            iat=NOW,
            exp=NOW + timedelta(minutes=60),
            jti="jti-1",
        )
    )


def make_use_case() -> AuthenticateToken:
    return AuthenticateToken(token_codec=JwtCodec(secret=SECRET, clock=FixedClock(NOW)))


def test_valid_token_builds_authenticated_user() -> None:
    result = make_use_case().execute(make_token())
    assert is_ok(result)
    assert result.value.id == uuid.UUID(int=10)
    assert result.value.role is Role.CUSTOMER
    assert result.value.customer_id == "123"


def test_unknown_role_claim_rejected() -> None:
    result = make_use_case().execute(make_token(role="superuser", customer_id=None))
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"


def test_customer_token_without_customer_id_rejected() -> None:
    """BR-1.3 seam: a customer identity always carries its ownership key."""
    result = make_use_case().execute(make_token(role="customer", customer_id=None))
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"


def test_malformed_sub_rejected() -> None:
    result = make_use_case().execute(make_token(sub="not-a-uuid"))
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"
