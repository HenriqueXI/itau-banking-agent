"""TracerPort: the only way this codebase emits traces (ADR-010, telemetry.md §1).

Langfuse SDK calls are not sprinkled through the code — everything goes through
these protocols, which buys three things the SDK cannot: a no-op adapter that
keeps unit tests infra-free (NFR-7), one masking chokepoint before export
(NFR-1), and a blast radius of one file when the SDK's API churns.

Shape, mirroring Langfuse's model:

    tracer.trace(TraceSpec(...))     # one per turn, grouped into a session
      └── scope.span("retrieve")     # a unit of work; nests arbitrarily
      └── scope.generation(...)      # an LLM call, carrying token usage

Every scope is a context manager: the span ends when the block exits, including
on an exception (which marks it ERROR). Nothing here ever raises — a tracer that
can break a turn is worse than no tracer (FR-7 is observability, not a
dependency), so adapters swallow and degrade to no-op.

`current_scope()` exposes the innermost open scope via a contextvar. It exists
for one reason: the LLM adapter must attach a generation to whatever node span
is running without every node passing a scope down into the port call.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


@dataclass(frozen=True, kw_only=True)
class TraceSpec:
    """One turn. `session_id` is the conversation thread — that grouping is what
    makes Langfuse show a conversation instead of a pile of turns."""

    name: str
    trace_id: str
    session_id: str | None = None
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    input: Any = None


@dataclass(frozen=True, kw_only=True)
class GenerationSpec:
    """One LLM call. `provider` is separate from `model` on purpose: the
    fallback chain means the provider that served a turn is a finding, not a
    setting (llm-providers.md, ADR-008)."""

    name: str
    provider: str
    model: str
    prompt_version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    input: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Scope(Protocol):
    """An open trace or span. Children nest under it; `update` annotates it."""

    @property
    def name(self) -> str: ...

    def span(
        self,
        name: str,
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> "AbstractContextManager[Scope]": ...

    def generation(self, spec: GenerationSpec) -> "AbstractContextManager[Generation]": ...

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: Level | None = None,
        status_message: str | None = None,
    ) -> None: ...


class Generation(Scope, Protocol):
    def record_completion(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """What the call actually cost, once it is known.

        Separate from `GenerationSpec` because none of it is knowable up front:
        the fallback chain picks the provider at call time, and the model and
        token counts come back *with* the answer (FR-7.1). Tokens are optional —
        a stream reports none, and zeros would read as "free" rather than
        "unknown". Called at end-of-call or end-of-stream; late usage must still
        land, so this updates the open generation rather than creating one.
        """
        ...


class TracerPort(Protocol):
    def trace(self, spec: TraceSpec) -> AbstractContextManager[Scope]:
        """Context manager yielding the turn's root scope."""
        ...

    def flush(self) -> None:
        """Best-effort drain of pending exports. For scripts and shutdown — the
        request path never waits on it."""
        ...


_current_scope: ContextVar[Scope | None] = ContextVar("current_scope", default=None)


def current_scope() -> Scope | None:
    """Innermost open scope, or None when nothing is traced."""
    return _current_scope.get()


def annotate(**metadata: Any) -> None:
    """Attach metadata to the innermost open span, or do nothing if untraced.

    The convenience over `current_scope()` that instrumented code should reach
    for: a node describing itself must never need a None check, or the check is
    what gets forgotten.
    """
    scope = current_scope()
    if scope is not None:
        scope.update(metadata=metadata)


@contextmanager
def use_scope(scope: Scope) -> Iterator[Scope]:
    """Make `scope` current for the enclosed block. Adapters wrap their span
    context managers with this; callers should not need it."""
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)
