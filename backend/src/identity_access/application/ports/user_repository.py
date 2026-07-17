"""UserRepository port."""

import uuid
from collections.abc import Collection
from typing import Protocol

from identity_access.domain.entities import User


class UserRepository(Protocol):
    async def get_by_email(self, email: str) -> User | None: ...

    async def get_by_id(self, user_id: uuid.UUID) -> User | None: ...

    async def list_by_ids(self, user_ids: Collection[uuid.UUID]) -> tuple[User, ...]: ...

    async def search_for_audit(self, query: str) -> tuple[User, ...]: ...
