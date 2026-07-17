"""Base type for expected, typed domain failures (returned, never raised)."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class DomainError:
    """A business-level failure a use case returns via Result.

    `code` is a stable machine-readable identifier (e.g. "authorization.denied");
    `message` is an internal English description — user-facing pt-BR copy is
    produced at the presentation boundary, never here.
    """

    code: str
    message: str
