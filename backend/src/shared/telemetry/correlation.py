"""The `trace_id` contextvar — one turn's identity, readable from anywhere.

FR-7.2 wants the same id on the trace, every log line, and every audit row.
Threading it through every signature would work right up to the first use case
that forgets; a contextvar makes "forgetting" impossible instead, and lets
`DomainEvent` stamp itself (shared/domain/events.py).

Deliberately stdlib-only: `shared.domain` imports this, and `domain/` imports no
frameworks (import-linter contract "Domain imports no frameworks").
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """uuid4 — no sequence, no collision assumptions (PRD-013 edge case)."""
    return str(uuid.uuid4())


def current_trace_id() -> str | None:
    """The trace id of the turn in flight, or None outside one (scripts, boot)."""
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> Token[str | None]:
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


@contextmanager
def trace_context(trace_id: str) -> Iterator[str]:
    """Bind `trace_id` for the enclosed block and restore the previous value.

    Restore rather than clear: nesting is not expected, but a turn that silently
    unbinds its caller's id would corrupt correlation in a way no test catches.
    """
    token = set_trace_id(trace_id)
    try:
        yield trace_id
    finally:
        reset_trace_id(token)
