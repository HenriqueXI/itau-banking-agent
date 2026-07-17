"""Per-user sliding-window rate limit for the agent endpoint (api.md).

Protects LLM quota and the abuse surface (NFR-5). In-memory and per-process:
the demo runs one API container, and a shared-store limiter is a PRD-015
decision, not something to improvise here. Documented as such in
known-limitations rather than silently pretended.
"""

from collections import defaultdict, deque

from shared.application.ports.clock import Clock

WINDOW_SECONDS = 60.0


class SlidingWindowRateLimiter:
    def __init__(self, *, clock: Clock, max_per_minute: int) -> None:
        self._clock = clock
        self._max = max_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        hits = self._hits[key]
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True
