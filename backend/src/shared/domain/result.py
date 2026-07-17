"""Result type: expected failures are values, not exceptions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeGuard


@dataclass(frozen=True)
class Ok[T]:
    value: T


@dataclass(frozen=True)
class Err[E]:
    error: E


type Result[T, E] = Ok[T] | Err[E]


def is_ok[T, E](result: Result[T, E]) -> TypeGuard[Ok[T]]:
    return isinstance(result, Ok)


def is_err[T, E](result: Result[T, E]) -> TypeGuard[Err[E]]:
    return isinstance(result, Err)


def map_ok[T, U, E](result: Result[T, E], fn: Callable[[T], U]) -> Result[U, E]:
    """Apply `fn` to the success value, passing errors through unchanged."""
    if isinstance(result, Ok):
        return Ok(fn(result.value))
    return result


def unwrap_or[T, E](result: Result[T, E], default: T) -> T:
    if isinstance(result, Ok):
        return result.value
    return default
