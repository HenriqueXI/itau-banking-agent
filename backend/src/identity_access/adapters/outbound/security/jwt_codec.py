"""HS256 JWT codec (PyJWT). Expiry checked against the injected Clock with
zero leeway (PRD-004 edge case: single-host demo, documented)."""

from datetime import UTC, datetime
from typing import Any

import jwt

from identity_access.application.dto import TokenClaims
from identity_access.domain.errors import token_expired, token_invalid
from shared.application.ports.clock import Clock
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result

_ALGORITHM = "HS256"


class JwtCodec:
    def __init__(self, *, secret: str, clock: Clock) -> None:
        self._secret = secret
        self._clock = clock

    def encode(self, claims: TokenClaims) -> str:
        payload: dict[str, Any] = {
            "sub": claims.sub,
            "role": claims.role,
            "exp": int(claims.exp.timestamp()),
            "iat": int(claims.iat.timestamp()),
            "jti": claims.jti,
        }
        if claims.customer_id is not None:
            payload["customer_id"] = claims.customer_id
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def decode(self, token: str) -> Result[TokenClaims, DomainError]:
        try:
            # Signature verified here; expiry checked below against our Clock
            # so tests control time deterministically.
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"verify_exp": False, "require": ["sub", "role", "exp", "iat", "jti"]},
            )
        except jwt.InvalidTokenError:
            return Err(token_invalid())

        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        if self._clock.now() >= exp:
            return Err(token_expired())

        return Ok(
            TokenClaims(
                sub=payload["sub"],
                role=payload["role"],
                customer_id=payload.get("customer_id"),
                exp=exp,
                iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
                jti=payload["jti"],
            )
        )
