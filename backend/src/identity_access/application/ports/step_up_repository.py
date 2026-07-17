"""StepUpChallengeRepository port. `get_for_update` must row-lock so
concurrent verifies of the same challenge serialize (PRD-004 edge case)."""

import uuid
from typing import Protocol

from identity_access.domain.entities import StepUpChallenge


class StepUpChallengeRepository(Protocol):
    async def add(self, challenge: StepUpChallenge) -> None: ...

    async def get_for_update(self, challenge_id: uuid.UUID) -> StepUpChallenge | None: ...

    async def save(self, challenge: StepUpChallenge) -> None:
        """Persist mutated attempts/used_at — called on every verify outcome."""
        ...
