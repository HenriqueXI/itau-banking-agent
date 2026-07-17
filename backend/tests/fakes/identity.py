"""In-memory fakes for identity_access ports (NFR-7)."""

import uuid
from collections.abc import Collection

from identity_access.domain.entities import StepUpChallenge, User


class FakePasswordHasher:
    """Reversible marker hashes — assertion-friendly, obviously not secure."""

    def __init__(self) -> None:
        self.dummy_verifications = 0

    def hash(self, plain: str) -> str:
        return f"hashed:{plain}"

    def verify(self, plain: str, hashed: str) -> bool:
        if hashed == self.dummy_hash():
            self.dummy_verifications += 1
            return False
        return hashed == f"hashed:{plain}"

    def dummy_hash(self) -> str:
        return "hashed:<dummy>"


class InMemoryUserRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self._users = {user.email: user for user in (users or [])}

    async def get_by_email(self, email: str) -> User | None:
        return self._users.get(email)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        for user in self._users.values():
            if user.id == user_id:
                return user
        return None

    async def list_by_ids(self, user_ids: Collection[uuid.UUID]) -> tuple[User, ...]:
        return tuple(user for user in self._users.values() if user.id in user_ids)

    async def search_for_audit(self, query: str) -> tuple[User, ...]:
        normalized = query.casefold()
        return tuple(
            user
            for user in self._users.values()
            if normalized in user.name.casefold()
            or normalized in user.email.casefold()
            or str(user.id).startswith(normalized)
        )


class InMemoryStepUpRepository:
    def __init__(self) -> None:
        self.challenges: dict[uuid.UUID, StepUpChallenge] = {}
        self.saves = 0

    async def add(self, challenge: StepUpChallenge) -> None:
        self.challenges[challenge.id] = challenge

    async def get_for_update(self, challenge_id: uuid.UUID) -> StepUpChallenge | None:
        return self.challenges.get(challenge_id)

    async def save(self, challenge: StepUpChallenge) -> None:
        self.challenges[challenge.id] = challenge
        self.saves += 1


class FixedCodeGenerator:
    def __init__(self, code: str = "123456") -> None:
        self._code = code

    def generate(self) -> str:
        return self._code
