"""TokenCodec port — keeps PyJWT out of application/domain code."""

from typing import Protocol

from identity_access.application.dto import TokenClaims
from shared.domain.errors import DomainError
from shared.domain.result import Result


class TokenCodec(Protocol):
    def encode(self, claims: TokenClaims) -> str: ...

    def decode(self, token: str) -> Result[TokenClaims, DomainError]:
        """Verify signature and expiry (zero leeway) and return the claims."""
        ...
