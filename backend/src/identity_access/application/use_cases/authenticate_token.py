"""Bearer token → AuthenticatedUser (PRD004-FR-3). Role from JWT only (BR-1.2)."""

import uuid

from identity_access.application.ports.token_codec import TokenCodec
from identity_access.domain.errors import token_invalid
from identity_access.domain.values import AuthenticatedUser, Role
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result


class AuthenticateToken:
    def __init__(self, *, token_codec: TokenCodec) -> None:
        self._codec = token_codec

    def execute(self, token: str) -> Result[AuthenticatedUser, DomainError]:
        decoded = self._codec.decode(token)
        if isinstance(decoded, Err):
            return decoded
        claims = decoded.value
        try:
            user = AuthenticatedUser(
                id=uuid.UUID(claims.sub),
                role=Role(claims.role),
                customer_id=claims.customer_id,
            )
        except ValueError:
            return Err(token_invalid())
        return Ok(user)
