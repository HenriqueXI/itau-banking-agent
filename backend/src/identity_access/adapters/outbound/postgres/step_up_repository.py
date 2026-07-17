"""SQLAlchemy StepUpChallengeRepository. `get_for_update` uses SELECT ... FOR
UPDATE so concurrent verifies of one challenge serialize on the row lock."""

import uuid

from sqlalchemy import Row, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from identity_access.adapters.outbound.postgres.tables import step_up_challenges
from identity_access.domain.entities import StepUpChallenge


def _to_entity(row: Row) -> StepUpChallenge:
    return StepUpChallenge(
        id=row.id,
        user_id=row.user_id,
        operation_hash=row.operation_hash,
        code_hash=row.code_hash,
        expires_at=row.expires_at,
        attempts=row.attempts,
        used_at=row.used_at,
    )


class PostgresStepUpChallengeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, challenge: StepUpChallenge) -> None:
        await self._session.execute(
            insert(step_up_challenges).values(
                id=challenge.id,
                user_id=challenge.user_id,
                operation_hash=challenge.operation_hash,
                code_hash=challenge.code_hash,
                expires_at=challenge.expires_at,
                attempts=challenge.attempts,
                used_at=challenge.used_at,
            )
        )

    async def get_for_update(self, challenge_id: uuid.UUID) -> StepUpChallenge | None:
        result = await self._session.execute(
            select(step_up_challenges)
            .where(step_up_challenges.c.id == challenge_id)
            .with_for_update()
        )
        row = result.one_or_none()
        return _to_entity(row) if row else None

    async def save(self, challenge: StepUpChallenge) -> None:
        await self._session.execute(
            update(step_up_challenges)
            .where(step_up_challenges.c.id == challenge.id)
            .values(attempts=challenge.attempts, used_at=challenge.used_at)
        )
