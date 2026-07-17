"""Demo login: verify credentials, mint a JWT (PRD004-FR-2)."""

from datetime import timedelta

from identity_access.application.dto import LoginCommand, LoginResult, TokenClaims
from identity_access.application.ports.password_hasher import PasswordHasher
from identity_access.application.ports.token_codec import TokenCodec
from identity_access.application.ports.user_repository import UserRepository
from identity_access.domain.errors import invalid_credentials
from shared.application.ports.clock import Clock
from shared.application.ports.id_generator import IdGenerator
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result


class Login:
    def __init__(
        self,
        *,
        users: UserRepository,
        password_hasher: PasswordHasher,
        token_codec: TokenCodec,
        clock: Clock,
        id_generator: IdGenerator,
        jwt_ttl_minutes: int,
    ) -> None:
        self._users = users
        self._hasher = password_hasher
        self._codec = token_codec
        self._clock = clock
        self._ids = id_generator
        self._ttl = timedelta(minutes=jwt_ttl_minutes)

    async def execute(self, command: LoginCommand) -> Result[LoginResult, DomainError]:
        user = await self._users.get_by_email(command.email)
        if user is None:
            # Burn comparable time so response latency doesn't reveal existence.
            self._hasher.verify(command.password, self._hasher.dummy_hash())
            return Err(invalid_credentials())
        if not self._hasher.verify(command.password, user.password_hash):
            return Err(invalid_credentials())

        now = self._clock.now()
        claims = TokenClaims(
            sub=str(user.id),
            role=user.role.value,
            customer_id=user.customer_id,
            iat=now,
            exp=now + self._ttl,
            jti=str(self._ids.new_id()),
        )
        return Ok(
            LoginResult(
                access_token=self._codec.encode(claims),
                token_type="bearer",  # nosec B106 — OAuth2 token type, not a password
                expires_in_seconds=int(self._ttl.total_seconds()),
            )
        )
