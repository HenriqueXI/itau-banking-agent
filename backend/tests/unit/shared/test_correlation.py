"""trace_id lifecycle: contextvar propagation into logs and events (FR-7.2).

The PRD's fourth acceptance row ("Log correlation | unit: contextvar
propagation"). The point of these tests is the *absence* of plumbing: nothing
here passes a trace_id to anything, and it still lands everywhere.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import ClassVar

import structlog

from shared.domain.events import DomainEvent
from shared.logging.setup import correlation_context
from shared.telemetry.correlation import current_trace_id, new_trace_id, trace_context
from tests.fakes import FixedClock, SequentialIdGenerator


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    event_type: ClassVar[str] = "shared.SomethingHappened"
    subject: str


def _event(subject: str = "s-1") -> SomethingHappened:
    return SomethingHappened(
        event_id=SequentialIdGenerator().new_id(), occurred_at=FixedClock().now(), subject=subject
    )


class TestTraceIdGeneration:
    def test_ids_are_uuid4_with_no_sequence_assumptions(self) -> None:
        """PRD-013 edge case ("trace id collision (paranoia)")."""
        ids = {new_trace_id() for _ in range(1000)}
        assert len(ids) == 1000
        assert all(uuid.UUID(value).version == 4 for value in ids)


class TestContextPropagation:
    def test_nothing_is_bound_outside_a_turn(self) -> None:
        assert current_trace_id() is None

    def test_the_id_is_readable_inside_the_block(self) -> None:
        with trace_context("tr-1"):
            assert current_trace_id() == "tr-1"

    def test_the_previous_value_is_restored_not_cleared(self) -> None:
        with trace_context("outer"):
            with trace_context("inner"):
                assert current_trace_id() == "inner"
            assert current_trace_id() == "outer"

    def test_the_id_is_released_when_the_block_raises(self) -> None:
        try:
            with trace_context("tr-1"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert current_trace_id() is None

    async def test_concurrent_turns_do_not_see_each_other_s_id(self) -> None:
        """Two turns in flight on one process must not cross-label. Contextvars
        are per-task, which is the entire reason this is safe."""
        seen: dict[str, str | None] = {}

        async def turn(name: str) -> None:
            with trace_context(name):
                await asyncio.sleep(0)  # force interleaving
                seen[name] = current_trace_id()

        await asyncio.gather(turn("a"), turn("b"))
        assert seen == {"a": "a", "b": "b"}


class TestLogCorrelation:
    def test_log_lines_inside_the_context_carry_the_trace_id(self) -> None:
        captured: list[dict[str, object]] = []

        def capture(logger: object, name: str, event_dict: dict[str, object]) -> str:
            captured.append(dict(event_dict))
            return ""

        structlog.configure(
            processors=[structlog.contextvars.merge_contextvars, capture],
            cache_logger_on_first_use=False,
        )
        try:
            log = structlog.get_logger("test")
            with correlation_context("tr-42", request_id="rq-1"):
                log.info("inside")
            log.info("outside")
        finally:
            structlog.reset_defaults()

        assert captured[0]["trace_id"] == "tr-42"
        assert captured[0]["request_id"] == "rq-1"
        assert "trace_id" not in captured[1], "the id must not outlive the turn"

    def test_correlation_context_binds_both_contextvars(self) -> None:
        """The structlog var and the domain-facing one are separate (domain may
        not import structlog) — `correlation_context` is what keeps them in step."""
        with correlation_context("tr-7"):
            assert current_trace_id() == "tr-7"
        assert current_trace_id() is None


class TestEventStamping:
    def test_an_event_raised_in_a_turn_stamps_itself(self) -> None:
        with trace_context("tr-9"):
            assert _event().trace_id == "tr-9"

    def test_an_event_raised_outside_a_turn_says_none_rather_than_guessing(self) -> None:
        assert _event().trace_id is None

    def test_the_trace_id_is_envelope_not_payload(self) -> None:
        """The outbox writes it as a column (PRD-014), so it must not also
        duplicate itself inside the payload body."""
        with trace_context("tr-9"):
            assert _event(subject="s-2").payload() == {"subject": "s-2"}

    def test_an_explicit_trace_id_still_wins(self) -> None:
        """Replaying or backfilling an event must be able to state the id."""
        with trace_context("tr-9"):
            event = SomethingHappened(
                event_id=uuid.UUID(int=1),
                occurred_at=FixedClock().now(),
                subject="s",
                trace_id="tr-explicit",
            )
        assert event.trace_id == "tr-explicit"
