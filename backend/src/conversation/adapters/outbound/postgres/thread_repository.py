"""SQLAlchemy ThreadRepository: claim-or-read on the primary key.

`claim` is an INSERT ... ON CONFLICT DO NOTHING followed by a read, so two
concurrent first-uses of the same thread id converge on one owner instead of
racing a read-then-write into two rows.
"""

import uuid

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from conversation.adapters.outbound.postgres.tables import conversation_threads
from conversation.domain.values import ConversationThread
from shared.application.ports.clock import Clock


def _to_entity(row: Row) -> ConversationThread:
    return ConversationThread(thread_id=row.thread_id, user_id=row.user_id)


class PostgresThreadRepository:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, thread_id: str) -> ConversationThread | None:
        result = await self._session.execute(
            select(conversation_threads).where(conversation_threads.c.thread_id == thread_id)
        )
        row = result.one_or_none()
        return _to_entity(row) if row else None

    async def claim(self, thread_id: str, user_id: uuid.UUID) -> ConversationThread:
        await self._session.execute(
            insert(conversation_threads)
            .values(thread_id=thread_id, user_id=user_id, created_at=self._clock.now())
            .on_conflict_do_nothing(index_elements=["thread_id"])
        )
        existing = await self.get(thread_id)
        if existing is None:  # unreachable: the insert above either wrote or conflicted
            raise RuntimeError(f"thread {thread_id!r} vanished between claim and read")
        return existing

    async def list_for_user(self, user_id: uuid.UUID) -> list[ConversationThread]:
        result = await self._session.execute(
            select(conversation_threads)
            .where(conversation_threads.c.user_id == user_id)
            .order_by(conversation_threads.c.created_at.desc())
        )
        return [_to_entity(row) for row in result.all()]
