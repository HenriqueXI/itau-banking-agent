"""SQLAlchemy UserRepository."""

import uuid
from collections.abc import Collection

from sqlalchemy import Row, Text, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from identity_access.adapters.outbound.postgres.tables import users
from identity_access.domain.entities import User
from identity_access.domain.values import Role


def _to_entity(row: Row) -> User:
    return User(
        id=row.id,
        email=row.email,
        name=row.name,
        role=Role(row.role),
        customer_id=row.customer_id,
        password_hash=row.password_hash,
        created_at=row.created_at,
    )


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(users).where(users.c.email == email))
        row = result.one_or_none()
        return _to_entity(row) if row else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(users).where(users.c.id == user_id))
        row = result.one_or_none()
        return _to_entity(row) if row else None

    async def list_by_ids(self, user_ids: Collection[uuid.UUID]) -> tuple[User, ...]:
        if not user_ids:
            return ()
        result = await self._session.execute(select(users).where(users.c.id.in_(user_ids)))
        return tuple(_to_entity(row) for row in result.all())

    async def search_for_audit(self, query: str) -> tuple[User, ...]:
        """Find actors by a literal case-insensitive name, email or UUID prefix."""
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not escaped:
            return ()
        contains = f"%{escaped}%"
        uuid_prefix = f"{escaped}%"
        result = await self._session.execute(
            select(users).where(
                or_(
                    users.c.name.ilike(contains, escape="\\"),
                    users.c.email.ilike(contains, escape="\\"),
                    cast(users.c.id, Text).ilike(uuid_prefix, escape="\\"),
                )
            )
        )
        return tuple(_to_entity(row) for row in result.all())
