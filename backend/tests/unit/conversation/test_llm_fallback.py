"""Fallback chain behavior (llm-providers.md §3, ADR-008)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from conversation.adapters.outbound.llm.fallback import BREAKER_THRESHOLD, FallbackLlm
from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    MessageRole,
)
from tests.fakes.providers import FixedClock

MESSAGES = [LlmMessage(role=MessageRole.USER, content="oi")]


class FlakyProvider:
    """Fails `failures` times, then answers. Records how often it was asked."""

    def __init__(self, name: str, *, failures: int = 0, retriable: bool = True) -> None:
        self._name = name
        self._failures = failures
        self._retriable = retriable
        self.attempts = 0

    @property
    def provider(self) -> str:
        return self._name

    async def complete(self, messages: list[LlmMessage], **kwargs: Any) -> Completion:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise LlmError("boom", provider=self._name, retriable=self._retriable)
        return Completion(text="ok", provider=self._name, model="m")

    async def stream(self, messages: list[LlmMessage], **kwargs: Any) -> AsyncIterator[Delta]:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise LlmError("boom", provider=self._name, retriable=self._retriable)
        yield Delta(text="ok", provider=self._name, model="m")


def _chain(*providers: FlakyProvider) -> FallbackLlm:
    return FallbackLlm(providers=list(providers), clock=FixedClock())


async def test_primary_answer_is_tagged_with_the_provider_that_served() -> None:
    chain = _chain(FlakyProvider("gemini"), FlakyProvider("openrouter"))
    assert (await chain.complete(MESSAGES)).provider == "gemini"


async def test_retriable_failure_retries_the_same_provider_first() -> None:
    primary = FlakyProvider("gemini", failures=1)
    secondary = FlakyProvider("openrouter")
    completion = await _chain(primary, secondary).complete(MESSAGES)

    assert completion.provider == "gemini"
    assert primary.attempts == 2
    assert secondary.attempts == 0


async def test_exhausted_provider_rotates_to_the_next_and_tags_it() -> None:
    primary = FlakyProvider("gemini", failures=5)
    secondary = FlakyProvider("openrouter")
    completion = await _chain(primary, secondary).complete(MESSAGES)

    assert completion.provider == "openrouter"


async def test_non_retriable_failure_surfaces_instead_of_burning_the_next_quota() -> None:
    """A 4xx is our bug — rotating providers would hide it behind a second bill."""
    primary = FlakyProvider("gemini", failures=1, retriable=False)
    secondary = FlakyProvider("openrouter")

    with pytest.raises(LlmError):
        await _chain(primary, secondary).complete(MESSAGES)
    assert primary.attempts == 1
    assert secondary.attempts == 0


async def test_all_providers_failing_raises_the_last_error() -> None:
    chain = _chain(FlakyProvider("gemini", failures=9), FlakyProvider("ollama", failures=9))
    with pytest.raises(LlmError):
        await chain.complete(MESSAGES)


async def test_breaker_opens_and_skips_a_dead_provider() -> None:
    """A dead provider must not tax every turn with its own timeout."""
    primary = FlakyProvider("gemini", failures=99)
    secondary = FlakyProvider("openrouter")
    chain = _chain(primary, secondary)

    for _ in range(BREAKER_THRESHOLD):
        await chain.complete(MESSAGES)
    attempts_before = primary.attempts

    await chain.complete(MESSAGES)
    assert primary.attempts == attempts_before  # skipped entirely


async def test_breaker_reopens_after_the_cooldown() -> None:
    primary = FlakyProvider("gemini", failures=BREAKER_THRESHOLD * 2)
    clock = FixedClock()
    chain = FallbackLlm(providers=[primary, FlakyProvider("openrouter")], clock=clock)

    for _ in range(BREAKER_THRESHOLD):
        await chain.complete(MESSAGES)
    clock.advance(seconds=61)
    await chain.complete(MESSAGES)

    assert primary.attempts > BREAKER_THRESHOLD  # tried again after cooldown


async def test_stream_fails_over_before_the_first_delta() -> None:
    """No tokens out yet, so rotating is safe — and the deltas carry the
    provider that actually served, not the one we asked for first."""
    primary = FlakyProvider("gemini", failures=1)
    chain = _chain(primary, FlakyProvider("openrouter"))

    deltas = [d async for d in chain.stream(MESSAGES)]
    assert [d.provider for d in deltas] == ["openrouter"]


async def test_stream_does_not_splice_two_providers_mid_answer() -> None:
    """Once tokens are out, switching provider would stitch two different
    answers into one message."""

    class BreaksMidStream:
        provider = "gemini"

        async def complete(self, messages: list[LlmMessage], **kwargs: Any) -> Completion:
            raise AssertionError("not used")

        async def stream(self, messages: list[LlmMessage], **kwargs: Any) -> AsyncIterator[Delta]:
            yield Delta(text="meia ", provider="gemini", model="m")
            raise LlmError("dropped", provider="gemini", retriable=True)

    chain = FallbackLlm(
        providers=[BreaksMidStream(), FlakyProvider("openrouter")], clock=FixedClock()
    )

    collected: list[str] = []
    with pytest.raises(LlmError):
        async for delta in chain.stream(MESSAGES):
            collected.append(delta.text)
    assert collected == ["meia "]


def test_empty_chain_is_a_wiring_error() -> None:
    with pytest.raises(ValueError):
        FallbackLlm(providers=[], clock=FixedClock())
