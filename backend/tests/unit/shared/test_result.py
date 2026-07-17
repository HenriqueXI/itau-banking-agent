from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, is_err, is_ok, map_ok, unwrap_or


def test_ok_holds_value() -> None:
    result: Ok[int] = Ok(42)
    assert is_ok(result)
    assert not is_err(result)
    assert result.value == 42


def test_err_holds_error() -> None:
    error = DomainError(code="limit.exceeded", message="daily limit exceeded")
    result: Err[DomainError] = Err(error)
    assert is_err(result)
    assert not is_ok(result)
    assert result.error.code == "limit.exceeded"


def test_map_ok_transforms_success() -> None:
    assert map_ok(Ok(2), lambda x: x * 10) == Ok(20)


def test_map_ok_passes_error_through() -> None:
    error = DomainError(code="x", message="y")
    assert map_ok(Err(error), lambda x: x) == Err(error)


def test_unwrap_or_returns_value_or_default() -> None:
    assert unwrap_or(Ok(1), 99) == 1
    assert unwrap_or(Err(DomainError(code="x", message="y")), 99) == 99


def test_results_are_immutable() -> None:
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        Ok(1).value = 2  # type: ignore[misc]
