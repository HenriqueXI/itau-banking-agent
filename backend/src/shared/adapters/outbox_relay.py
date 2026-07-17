"""Background relay from durable outbox rows to the in-process bus."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.adapters.outbox import PostgresOutboxRepository
from shared.application.ports.clock import Clock
from shared.application.ports.event_bus import EventBus

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RelayResult:
    processed: int = 0
    failed: int = 0


class OutboxRelay:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        clock: Clock,
        batch_size: int,
        max_attempts: int,
        max_backoff_seconds: int,
    ) -> None:
        self._sessions = session_factory
        self._bus = event_bus
        self._clock = clock
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._max_backoff_seconds = max_backoff_seconds

    def retry_delay_seconds(self, previous_attempts: int) -> int:
        return min(2**previous_attempts, self._max_backoff_seconds)

    async def run_once(self) -> RelayResult:
        processed = failed = 0
        # The row lock spans delivery, preventing a second backend from
        # dispatching the same row while this process awaits its handler.
        async with self._sessions() as session, session.begin():
            repository = PostgresOutboxRepository(session)
            now = self._clock.now()
            events = await repository.claim_pending(now=now, limit=self._batch_size)
            for event in events:
                try:
                    await self._bus.dispatch(event)
                except Exception as exc:
                    next_attempt = event.attempts + 1
                    dead_letter = next_attempt >= self._max_attempts
                    retry_at = now + timedelta(seconds=self.retry_delay_seconds(event.attempts))
                    await repository.mark_failed(
                        event.event_id,
                        error=f"{type(exc).__name__}: {exc}",
                        next_attempt_at=retry_at,
                        dead_letter=dead_letter,
                    )
                    failed += 1
                    logger.exception(
                        "outbox.delivery_failed",
                        event_id=str(event.event_id),
                        event_type=event.event_type,
                        attempts=next_attempt,
                        dead_letter=dead_letter,
                    )
                else:
                    await repository.mark_processed(event.event_id, processed_at=now)
                    processed += 1
        return RelayResult(processed=processed, failed=failed)

    async def run(self, *, stop: asyncio.Event, interval_seconds: float) -> None:
        """Supervised loop: one broken iteration never kills deliveries forever."""
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("outbox.relay_iteration_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
        await self.run_once()
