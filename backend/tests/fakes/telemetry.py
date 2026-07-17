"""Telemetry fakes, at two different levels — and the difference matters.

`RecordingTracer` implements `TracerPort` and remembers calls: it asserts the
*contract* (span names, nesting, token usage) with no adapter in the way.

`StubSdk` impersonates the Langfuse SDK *underneath* the real adapter: it is the
only way to assert what would actually cross the wire, which is why every
masking test uses it. A recording port fake cannot prove masking — the port
carries raw values by design and the adapter is the door (ADR-010).

Together they replace the "integration test against local Langfuse" option in
the PRD-013 acceptance table with the "SDK stub asserting calls" one: no
container needed to prove a turn is traced.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from shared.application.ports.tracer import (
    GenerationSpec,
    Level,
    Scope,
    TraceSpec,
    use_scope,
)


@dataclass
class RecordedSpan:
    name: str
    kind: str  # "trace" | "span" | "generation"
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    level: Level | None = None
    status_message: str | None = None
    usage: tuple[int, int] | None = None
    provider: str | None = None
    """Who actually served a generation — set by `record_completion`, so it is
    the answered-by provider, not the configured one."""

    model: str | None = None
    parent: "RecordedSpan | None" = None
    generation: GenerationSpec | None = None

    @property
    def path(self) -> str:
        """`turn/understand` — asserting nesting without walking parents by hand."""
        return f"{self.parent.path}/{self.name}" if self.parent else self.name


class RecordingScope:
    def __init__(self, record: RecordedSpan, spans: list[RecordedSpan]) -> None:
        self._record = record
        self._spans = spans

    @property
    def name(self) -> str:
        return self._record.name

    @property
    def record(self) -> RecordedSpan:
        return self._record

    def _add(self, record: RecordedSpan) -> RecordedSpan:
        record.parent = self._record
        self._spans.append(record)
        return record

    @contextmanager
    def span(
        self,
        name: str,
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[Scope]:
        record = self._add(RecordedSpan(name=name, kind="span", input=input))
        record.metadata.update(metadata or {})
        with use_scope(RecordingScope(record, self._spans)) as scope:
            yield scope

    @contextmanager
    def generation(self, spec: GenerationSpec) -> Iterator["RecordingGeneration"]:
        record = self._add(
            RecordedSpan(name=spec.name, kind="generation", input=spec.input, generation=spec)
        )
        record.metadata.update(spec.metadata)
        generation = RecordingGeneration(record, self._spans)
        with use_scope(generation):
            yield generation

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: Level | None = None,
        status_message: str | None = None,
    ) -> None:
        if output is not None:
            self._record.output = output
        if metadata:
            self._record.metadata.update(metadata)
        if level is not None:
            self._record.level = level
        if status_message is not None:
            self._record.status_message = status_message


class RecordingGeneration(RecordingScope):
    def record_completion(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self._record.provider = provider
        self._record.model = model
        if input_tokens is not None or output_tokens is not None:
            self._record.usage = (input_tokens or 0, output_tokens or 0)


class RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []
        self.traces: list[RecordedSpan] = []
        self.flushes = 0

    @contextmanager
    def trace(self, spec: TraceSpec) -> Iterator[Scope]:
        record = RecordedSpan(name=spec.name, kind="trace", input=spec.input)
        record.metadata.update({**spec.metadata, "_spec": spec})
        self.spans.append(record)
        self.traces.append(record)
        with use_scope(RecordingScope(record, self.spans)) as scope:
            yield scope

    def flush(self) -> None:
        self.flushes += 1

    # -- assertions helpers ------------------------------------------------
    def paths(self) -> list[str]:
        return [span.path for span in self.spans]

    def named(self, name: str) -> RecordedSpan:
        matches = [span for span in self.spans if span.name == name]
        assert matches, f"no span named {name!r}; recorded: {self.paths()}"
        return matches[0]

    def generations(self) -> list[RecordedSpan]:
        return [span for span in self.spans if span.kind == "generation"]

    def payloads(self) -> list[Any]:
        """Everything the port was handed. NOT an export surface — these values
        are pre-masking by design; use `StubSdk` to assert what leaves."""
        return [
            value
            for span in self.spans
            for value in (span.input, span.output, span.metadata, span.status_message)
        ]


class StubStateful:
    """A Langfuse stateful client (trace/span/generation) that records instead
    of shipping. `fail=True` makes every SDK call raise — which is exactly what
    a Langfuse outage looks like from inside the adapter."""

    def __init__(self, kind: str, payload: dict[str, Any], *, fail: bool, log: list[Any]) -> None:
        self.kind = kind
        self.payload = payload
        self._fail = fail
        self._log = log
        self.children: list[StubStateful] = []
        self.updates: list[dict[str, Any]] = []
        self.ends: list[dict[str, Any]] = []
        log.append(self)

    def _guard(self) -> None:
        if self._fail:
            raise ConnectionError("langfuse unreachable")

    def _child(self, kind: str, kwargs: dict[str, Any]) -> "StubStateful":
        self._guard()
        child = StubStateful(kind, kwargs, fail=self._fail, log=self._log)
        self.children.append(child)
        return child

    def span(self, **kwargs: Any) -> "StubStateful":
        return self._child("span", kwargs)

    def generation(self, **kwargs: Any) -> "StubStateful":
        return self._child("generation", kwargs)

    def update(self, **kwargs: Any) -> None:
        self._guard()
        self.updates.append(kwargs)

    def end(self, **kwargs: Any) -> None:
        self._guard()
        self.ends.append(kwargs)


class StubSdk:
    """Stands in for `langfuse.Langfuse`, under the real adapter."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.sent: list[StubStateful] = []
        self.flushed = 0

    def trace(self, **kwargs: Any) -> StubStateful:
        if self._fail:
            raise ConnectionError("langfuse unreachable")
        return StubStateful("trace", kwargs, fail=self._fail, log=self.sent)

    def flush(self) -> None:
        if self._fail:
            raise ConnectionError("langfuse unreachable")
        self.flushed += 1

    def of_kind(self, kind: str) -> list[StubStateful]:
        return [item for item in self.sent if item.kind == kind]

    def everything(self) -> str:
        """Every payload, update and end that would have crossed the wire —
        the surface a leak test scans."""
        return "\n".join(
            repr(part) for item in self.sent for part in (item.payload, item.updates, item.ends)
        )
