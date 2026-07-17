"""Identity providers wired at the composition root (backend/README rule 4:
only the container and `api/` bind adapters to ports)."""

from dataclasses import dataclass

from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
from identity_access.adapters.outbound.security.code_generator import SecretsCodeGenerator
from identity_access.adapters.outbound.security.jwt_codec import JwtCodec
from identity_access.application.ports.code_generator import StepUpCodeGenerator
from identity_access.application.ports.password_hasher import PasswordHasher
from identity_access.application.ports.token_codec import TokenCodec
from shared.adapters.event_publisher import PostgresEventPublisher
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.config import Settings


@dataclass(frozen=True)
class IdentityProviders:
    password_hasher: PasswordHasher
    token_codec: TokenCodec
    code_generator: StepUpCodeGenerator
    event_publisher: EventPublisher

    @classmethod
    def build(cls, settings: Settings, clock: Clock) -> "IdentityProviders":
        return cls(
            password_hasher=Argon2PasswordHasher(),
            token_codec=JwtCodec(secret=settings.jwt_secret, clock=clock),
            code_generator=SecretsCodeGenerator(),
            event_publisher=PostgresEventPublisher(),
        )
