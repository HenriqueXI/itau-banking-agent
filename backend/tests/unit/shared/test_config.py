"""Config fail-loud contract (PRD001-FR-2): required vars crash with the var
named; optional vars default."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.config import Settings

REQUIRED = {"DATABASE_URL": "postgresql+asyncpg://app:app@localhost:5432/app", "JWT_SECRET": "s"}


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.upper() in REQUIRED or name.upper() in {
            "ENV",
            "LOG_LEVEL",
            "PIX_DAILY_LIMIT",
            "JWT_TTL_MINUTES",
            "CARD_LIMIT_MAXIMUMS",
        }:
            monkeypatch.delenv(name, raising=False)


def test_missing_required_var_fails_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "s")  # DATABASE_URL intentionally absent

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert "database_url" in str(excinfo.value)


def test_all_required_missing_names_every_var(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    message = str(excinfo.value)
    assert "database_url" in message
    assert "jwt_secret" in message


def test_optional_vars_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.env == "local"
    assert settings.jwt_ttl_minutes == 60
    assert settings.pix_daily_limit == Decimal("5000")
    assert settings.pix_stepup_threshold == Decimal("1000")
    assert settings.rate_limit_per_minute == 30
    assert settings.outbox_relay_interval_seconds == 1.0
    assert settings.outbox_relay_batch_size == 100
    assert settings.outbox_max_attempts == 5
    assert settings.outbox_max_backoff_seconds == 60


def test_card_limit_maximums_default_matches_br2_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.card_limit_maximums["Personnalité"] == (
        Decimal("50000"),
        Decimal("25000"),
        Decimal("10000"),
    )
    assert settings.card_limit_maximums["Uniclass"] == (
        Decimal("30000"),
        Decimal("15000"),
        Decimal("8000"),
    )
    assert settings.card_limit_maximums["Varejo"] == (
        Decimal("15000"),
        Decimal("8000"),
        Decimal("4000"),
    )


def test_card_limit_maximums_parsed_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "CARD_LIMIT_MAXIMUMS",
        '{"Personnalité": ["99000", "50000", "20000"], "Varejo": ["10000", "5000", "2000"]}',
    )

    settings = Settings(_env_file=None)

    assert settings.card_limit_maximums["Personnalité"] == (
        Decimal("99000"),
        Decimal("50000"),
        Decimal("20000"),
    )
    assert all(isinstance(v, Decimal) for row in settings.card_limit_maximums.values() for v in row)


def test_money_settings_are_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PIX_DAILY_LIMIT", "1234.56")

    settings = Settings(_env_file=None)

    assert settings.pix_daily_limit == Decimal("1234.56")
    assert isinstance(settings.pix_daily_limit, Decimal)


def test_invalid_env_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENV", "production-ish")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_docs_only_in_local(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    assert Settings(_env_file=None).docs_enabled is True
    monkeypatch.setenv("ENV", "demo")
    assert Settings(_env_file=None).docs_enabled is False
