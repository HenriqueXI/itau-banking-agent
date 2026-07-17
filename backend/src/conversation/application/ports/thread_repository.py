"""ThreadRepositoryPort: durable thread ↔ owner binding (PRD006-FR-6).

Ownership can't live in graph state alone: state is what a run reads *after* we
decide the caller may open the thread. The binding is claimed on first use and
never changes hands.
"""

import uuid
from typing import Protocol

from conversation.domain.values import ConversationThread


class ThreadRepositoryPort(Protocol):
    async def get(self, thread_id: str) -> ConversationThread | None: ...

    async def claim(self, thread_id: str, user_id: uuid.UUID) -> ConversationThread:
        """Bind the thread to `user_id`, or return the existing binding.

        Concurrent first-use of one thread id must resolve to a single owner —
        implementations rely on the primary key, not a read-then-write.
        """
        ...

    async def list_for_user(self, user_id: uuid.UUID) -> list[ConversationThread]: ...
