"""Shared test fakes (NFR-7). Fakes over mocks: real behavior, in memory."""

from tests.fakes.database import FakeEngine
from tests.fakes.providers import (
    FixedClock,
    RecordingEventPublisher,
    SequentialIdGenerator,
)
from tests.fakes.telemetry import RecordedSpan, RecordingTracer, StubSdk, StubStateful

__all__ = [
    "FakeEngine",
    "FixedClock",
    "RecordedSpan",
    "RecordingEventPublisher",
    "RecordingTracer",
    "SequentialIdGenerator",
    "StubSdk",
    "StubStateful",
]
