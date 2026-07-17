"""JWT lifecycle: claims roundtrip and expiry against a fake Clock (PRD-004)."""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from tests.fakes.providers import FixedClock

from identity_access.adapters.outbound.security.jwt_codec import JwtCodec
from identity_access.application.dto import TokenClaims
from shared.domain.result import is_err, is_ok

SECRET = "unit-test-secret-0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


def make_claims(**overrides) -> TokenClaims:
    defaults = dict(
        sub="00000000-0000-0000-0000-00000000000a",
        role="customer",
        customer_id="123",
        iat=NOW,
        exp=NOW + timedelta(minutes=60),
        jti="jti-1",
    )
    defaults.update(overrides)
    return TokenClaims(**defaults)


def make_codec(clock: FixedClock | None = None) -> JwtCodec:
    return JwtCodec(secret=SECRET, clock=clock or FixedClock(NOW))


def test_roundtrip_preserves_claims() -> None:
    codec = make_codec()
    decoded = codec.decode(codec.encode(make_claims()))
    assert is_ok(decoded)
    assert decoded.value == make_claims()


def test_expired_token_rejected_with_zero_leeway() -> None:
    clock = FixedClock(NOW)
    codec = make_codec(clock)
    token = codec.encode(make_claims())
    clock.advance(minutes=60)  # exactly at exp — zero leeway means rejected
    result = codec.decode(token)
    assert is_err(result)
    assert result.error.code == "auth.token_expired"


def test_wrong_signature_rejected() -> None:
    other = JwtCodec(
        secret="other-secret-0123456789abcdef0123456789abcdef",
        clock=FixedClock(NOW),
    )
    token = other.encode(make_claims())
    result = make_codec().decode(token)
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"


def test_garbage_token_rejected() -> None:
    result = make_codec().decode("not.a.jwt")
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"


def test_token_missing_required_claims_rejected() -> None:
    token = pyjwt.encode({"sub": "x", "exp": int((NOW + timedelta(hours=1)).timestamp())}, SECRET)
    result = make_codec().decode(token)
    assert is_err(result)
    assert result.error.code == "auth.token_invalid"


def test_customer_id_omitted_for_non_customers() -> None:
    codec = make_codec()
    token = codec.encode(make_claims(role="manager", customer_id=None))
    # verify_exp off: this raw decode inspects the payload shape, not validity.
    payload = pyjwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert "customer_id" not in payload
