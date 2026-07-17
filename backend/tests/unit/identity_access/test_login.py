"""Login use case: JWT claims, expiry, and no-leak credential errors."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.identity import FakePasswordHasher, InMemoryUserRepository
from tests.fakes.providers import FixedClock, SequentialIdGenerator

from identity_access.application.dto import LoginCommand, TokenClaims
from identity_access.application.use_cases.login import Login
from identity_access.domain.entities import User
from identity_access.domain.values import Role
from shared.domain.result import is_err, is_ok


class RecordingCodec:
    """Captures the claims passed to encode."""

    def __init__(self) -> None:
        self.encoded: list[TokenClaims] = []

    def encode(self, claims: TokenClaims) -> str:
        self.encoded.append(claims)
        return "token-1"

    def decode(self, token: str):  # pragma: no cover - unused in these tests
        raise NotImplementedError


ANA = User(
    id=uuid.UUID(int=10),
    email="ana@demo",
    name="Ana",
    role=Role.CUSTOMER,
    customer_id="123",
    password_hash="hashed:demo123",
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
)


@pytest.fixture
def codec() -> RecordingCodec:
    return RecordingCodec()


@pytest.fixture
def hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture
def use_case(codec: RecordingCodec, hasher: FakePasswordHasher) -> Login:
    return Login(
        users=InMemoryUserRepository([ANA]),
        password_hasher=hasher,
        token_codec=codec,
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
        jwt_ttl_minutes=60,
    )


async def test_valid_credentials_issue_jwt_with_persona_claims(
    use_case: Login, codec: RecordingCodec
) -> None:
    """Acceptance: Ana's token carries {role: customer, customer_id: "123"}."""
    result = await use_case.execute(LoginCommand(email="ana@demo", password="demo123"))
    assert is_ok(result)
    assert result.value.access_token == "token-1"
    assert result.value.expires_in_seconds == 3600

    claims = codec.encoded[0]
    assert claims.sub == str(ANA.id)
    assert claims.role == "customer"
    assert claims.customer_id == "123"
    assert claims.exp - claims.iat == timedelta(minutes=60)
    assert claims.jti  # recorded for the future revocation seam


async def test_wrong_password_returns_invalid_credentials(use_case: Login) -> None:
    result = await use_case.execute(LoginCommand(email="ana@demo", password="wrong"))
    assert is_err(result)
    assert result.error.code == "auth.invalid_credentials"


async def test_unknown_user_returns_identical_error(use_case: Login) -> None:
    """No user-existence leak: same DomainError as wrong password."""
    unknown = await use_case.execute(LoginCommand(email="ghost@demo", password="demo123"))
    wrong = await use_case.execute(LoginCommand(email="ana@demo", password="wrong"))
    assert is_err(unknown) and is_err(wrong)
    assert unknown.error == wrong.error


async def test_unknown_user_burns_a_dummy_verification(
    use_case: Login, hasher: FakePasswordHasher
) -> None:
    """Timing equalization: hasher runs even when the user does not exist."""
    await use_case.execute(LoginCommand(email="ghost@demo", password="demo123"))
    assert hasher.dummy_verifications == 1
