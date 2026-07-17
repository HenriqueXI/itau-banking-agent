"""No-op tracer: the default when Langfuse isn't configured, and the tracer
every unit test gets (NFR-7 — unit tests need no infrastructure).

It is not a stub that "does nothing": it still opens and nests scopes, so code
paths that read `current_scope()` behave identically with and without Langfuse.
A no-op that skipped the contextvar would make the traced path untested.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from shared.application.ports.tracer import (
    GenerationSpec,
    Level,
    Scope,
    TraceSpec,
    use_scope,
)


class NoopScope:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @contextmanager
    def span(
        self,
        name: str,
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[Scope]:
        with use_scope(NoopScope(name)) as scope:
            yield scope

    @contextmanager
    def generation(self, spec: GenerationSpec) -> Iterator["NoopGeneration"]:
        generation = NoopGeneration(spec.name)
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
        return None


class NoopGeneration(NoopScope):
    def record_completion(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        return None


class NoopTracer:
    @contextmanager
    def trace(self, spec: TraceSpec) -> Iterator[Scope]:
        with use_scope(NoopScope(spec.name)) as scope:
            yield scope

    def flush(self) -> None:
        return None
