"""Fallback chain: retry, rotate, and tag the provider that actually served.

Implements llm-providers.md §3 as a decorator over `LlmPort`, so nodes stay
unaware there are several providers at all:

- **Retriable (429/5xx):** exponential backoff (x2) within the provider, then the
  next provider in `LLM_FALLBACK_ORDER`.
- **Non-retriable (4xx):** no retry, no rotation — a bad request is our bug and
  rotating providers would only hide it (and burn a second quota).
- **Circuit breaker:** after repeated failures a provider is skipped for 60s, so
  a dead provider doesn't tax every turn with its own timeout.

`provider` on the returned Completion/Delta is the one that answered, not the
one we hoped for — telemetry needs the truth, not the intent.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    LlmPort,
)
from shared.application.ports.clock import Clock

logger = structlog.get_logger(__name__)

BACKOFF_BASE_SECONDS = 0.5
MAX_ATTEMPTS_PER_PROVIDER = 2
BREAKER_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 60.0


class _Breaker:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, provider: str) -> bool:
        opened = self._opened_at.get(provider)
        if opened is None:
            return False
        if self._now() - opened >= BREAKER_OPEN_SECONDS:
            self._failures.pop(provider, None)
            self._opened_at.pop(provider, None)
            return False
        return True

    def record_failure(self, provider: str) -> None:
        count = self._failures.get(provider, 0) + 1
        self._failures[provider] = count
        if count >= BREAKER_THRESHOLD:
            self._opened_at[provider] = self._now()
            logger.warning("llm.breaker_open", provider=provider, failures=count)

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._opened_at.pop(provider, None)

    def _now(self) -> float:
        return self._clock.now().timestamp()


class FallbackLlm:
    """Ordered chain of providers. `providers` is already in fallback order."""

    def __init__(self, *, providers: list[LlmPort], clock: Clock) -> None:
        if not providers:
            raise ValueError("fallback chain needs at least one provider")
        self._providers = providers
        self._breaker = _Breaker(clock)

    @property
    def provider(self) -> str:
        return self._providers[0].provider

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion:
        last_error: LlmError | None = None

        for provider in self._eligible():
            for attempt in range(1, MAX_ATTEMPTS_PER_PROVIDER + 1):
                try:
                    completion = await provider.complete(
                        messages,
                        json_schema=json_schema,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except LlmError as error:
                    last_error = error
                    self._breaker.record_failure(provider.provider)
                    logger.warning(
                        "llm.attempt_failed",
                        provider=provider.provider,
                        attempt=attempt,
                        retriable=error.retriable,
                        error=str(error),
                    )
                    if not error.retriable:
                        raise  # surface the bug instead of burning the next provider
                    if attempt < MAX_ATTEMPTS_PER_PROVIDER:
                        await asyncio.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                    continue
                else:
                    self._breaker.record_success(provider.provider)
                    return completion

        raise last_error or LlmError(
            "no provider available", provider=self.provider, retriable=True
        )

    async def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]:
        """Failover only before the first delta: once tokens are out, switching
        provider mid-answer would splice two different answers together."""
        last_error: LlmError | None = None

        for provider in self._eligible():
            started = False
            try:
                async for delta in provider.stream(
                    messages, temperature=temperature, max_tokens=max_tokens
                ):
                    started = True
                    yield delta
            except LlmError as error:
                last_error = error
                self._breaker.record_failure(provider.provider)
                if started or not error.retriable:
                    raise
                continue
            else:
                self._breaker.record_success(provider.provider)
                return

        raise last_error or LlmError(
            "no provider available", provider=self.provider, retriable=True
        )

    def _eligible(self) -> list[LlmPort]:
        """Skip open breakers — unless they're all open, in which case try
        anyway: a stale breaker must not turn a recovering outage into a
        permanent one."""
        eligible = [p for p in self._providers if not self._breaker.is_open(p.provider)]
        return eligible or self._providers
