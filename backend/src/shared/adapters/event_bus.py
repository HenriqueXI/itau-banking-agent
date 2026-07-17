"""Small in-process event bus; delivery is intentionally at-least-once."""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable

from shared.domain.outbox import StoredEvent

EventHandler = Callable[[StoredEvent], Awaitable[None]]


class UnknownEventVersionError(RuntimeError):
    pass


class InMemoryEventBus:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], list[EventHandler]] = defaultdict(list)

    def register(self, *, event_type: str, version: int, handler: EventHandler) -> None:
        self._handlers[(event_type, version)].append(handler)

    def assert_complete(self, supported: Iterable[tuple[str, int]]) -> None:
        missing = [entry for entry in supported if not self._handlers.get(entry)]
        if missing:
            raise RuntimeError(f"Event handlers missing for: {missing!r}")

    async def dispatch(self, event: StoredEvent) -> None:
        handlers = self._handlers.get((event.event_type, event.version), [])
        if not handlers:
            raise UnknownEventVersionError(f"Unsupported event {event.event_type}@{event.version}")
        for handler in handlers:
            await handler(event)
