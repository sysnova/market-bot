import json

import pytest
from pydantic import SecretStr, ValidationError

from app.common.settings import AppSettings, Environment


def test_settings_read_marketbot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETBOT_ENVIRONMENT", "development")
    monkeypatch.setenv("MARKETBOT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MARKETBOT_DATABASE_URL", "postgresql://user:password@db/marketbot")

    settings = AppSettings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "DEBUG"
    assert settings.database_url.get_secret_value().endswith("@db/marketbot")
    assert settings.entry_watcher_enabled is True
    assert settings.entry_watch_ttl_days == 56


def test_settings_reject_unknown_constructor_keys() -> None:
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, unknown="surprise")  # type: ignore[call-arg]


def test_secret_values_are_redacted_from_repr_and_json() -> None:
    settings = AppSettings(
        _env_file=None,
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


def test_alpaca_settings_load_as_paired_redacted_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKETBOT_ALPACA_API_KEY_ID", "paper-key-id")
    monkeypatch.setenv("MARKETBOT_ALPACA_API_SECRET_KEY", "paper-secret-key")
    monkeypatch.setenv("MARKETBOT_ALPACA_DATA_FEED", "sip")
    monkeypatch.setenv("MARKETBOT_ALPACA_WATCHLIST", "AAPL, msft,AAPL , NVDA")

    settings = AppSettings(_env_file=None)

    assert settings.alpaca_configured is True
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    assert settings.alpaca_api_key_id.get_secret_value() == "paper-key-id"
    assert settings.alpaca_api_secret_key.get_secret_value() == "paper-secret-key"
    assert settings.alpaca_data_feed == "sip"
    assert settings.alpaca_adjustment == "split"
    assert settings.alpaca_symbols == ("AAPL", "MSFT", "NVDA")
    assert settings.alpaca_execution_enabled is False
    serialized = json.dumps(settings.redacted())
    assert "paper-key-id" not in serialized
    assert "paper-secret-key" not in serialized


@pytest.mark.parametrize(
    "provided_key",
    ["MARKETBOT_ALPACA_API_KEY_ID", "MARKETBOT_ALPACA_API_SECRET_KEY"],
)
def test_alpaca_credentials_must_be_configured_as_a_pair(
    monkeypatch: pytest.MonkeyPatch,
    provided_key: str,
) -> None:
    monkeypatch.setenv(provided_key, "orphaned-credential")

    with pytest.raises(ValidationError, match="configured together"):
        AppSettings(_env_file=None)


def test_alpaca_execution_cannot_be_enabled_in_analysis_only_mvp() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            _env_file=None,
            alpaca_execution_enabled=True,
        )


def test_alpaca_watchlist_rejects_invalid_symbols() -> None:
    with pytest.raises(ValidationError, match="watchlist"):
        AppSettings(_env_file=None, alpaca_watchlist="AAPL,../BAD")


def test_sec_settings_require_an_identifiable_user_agent_when_enabled() -> None:
    with pytest.raises(ValidationError, match="SEC user agent"):
        AppSettings(_env_file=None, sec_enabled=True)

    settings = AppSettings(
        _env_file=None,
        sec_enabled=True,
        sec_user_agent="MarketBot/0.1 research@example.com",
    )

    assert settings.sec_configured is True
    assert settings.sec_refresh_hours == 6
    assert settings.sec_filing_lookback_days == 2


def test_supabase_universe_credentials_are_paired_and_redacted() -> None:
    settings = AppSettings(
        _env_file=None,
        supabase_url="https://example.supabase.co",
        supabase_desktop_api_key=SecretStr("desktop-secret"),
        universe_refresh_seconds=120,
    )

    assert settings.supabase_universe_configured is True
    assert "desktop-secret" not in json.dumps(settings.redacted())


@pytest.mark.parametrize("field", ["supabase_url", "supabase_desktop_api_key"])
def test_supabase_universe_credentials_must_be_configured_as_a_pair(field: str) -> None:
    with pytest.raises(ValidationError, match="Supabase URL and desktop API key"):
        AppSettings(_env_file=None, **{field: "https://example.supabase.co"})
