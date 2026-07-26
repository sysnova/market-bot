import json

import pytest
from pydantic import SecretStr, ValidationError

from app.common.settings import AppSettings, Environment


def test_settings_read_marketbot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETBOT_ENVIRONMENT", "development")
    monkeypatch.setenv("MARKETBOT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MARKETBOT_DATABASE_URL", "postgresql://user:password@db/marketbot")

    settings = AppSettings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "DEBUG"
    assert settings.database_url.get_secret_value().endswith("@db/marketbot")


def test_settings_reject_unknown_constructor_keys() -> None:
    with pytest.raises(ValidationError):
        AppSettings(unknown="surprise")  # type: ignore[call-arg]


def test_secret_values_are_redacted_from_repr_and_json() -> None:
    settings = AppSettings(
        database_url=SecretStr("postgresql://user:password@db/marketbot"),
        nats_url=SecretStr("nats://token@nats:4222"),
    )

    rendered = repr(settings)
    serialized = json.dumps(settings.redacted())

    assert "password" not in rendered
    assert "token" not in rendered
    assert "password" not in serialized
    assert "token" not in serialized
    assert serialized.count("**********") == 2
