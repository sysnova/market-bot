import json
from decimal import Decimal
from pathlib import Path

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
    assert settings.alert_checkpoint_interval_seconds == 30
    assert settings.definition_path == Path("configs/marketbot/7.34.0.yaml")
    assert settings.news_intelligence_model == "gpt-5.4-nano-2026-03-17"
    assert settings.news_intelligence_refresh_seconds == 300
    assert settings.openai_configured is False
    assert settings.entry_confirmation_rule_version is None


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

    settings = AppSettings(_env_file=None)

    assert settings.alpaca_configured is True
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    assert settings.alpaca_api_key_id.get_secret_value() == "paper-key-id"
    assert settings.alpaca_api_secret_key.get_secret_value() == "paper-secret-key"
    assert settings.alpaca_data_feed == "sip"
    assert settings.alpaca_adjustment == "split"
    assert settings.alpaca_rest_batch_size == 20
    assert settings.alpaca_stream_handshake_timeout_seconds == 20
    assert settings.alpaca_stream_reconnect_initial_seconds == 1
    assert settings.alpaca_stream_reconnect_max_seconds == 300
    assert settings.alpaca_stream_stable_seconds == 60
    assert settings.alpaca_stream_recovery_buffer_bars == 100_000
    assert settings.microstructure_max_symbols == 40
    assert settings.market_history_refresh_seconds == 3600
    assert settings.market_history_request_timeout_seconds == 1800
    assert settings.options_gamma_refresh_seconds == 600
    assert settings.options_gamma_days_forward == 45
    assert settings.options_gamma_strike_range_percent == Decimal("50")
    assert settings.options_gamma_concurrency == 4
    assert str(settings.alpaca_options_contracts_base_url) == ("https://paper-api.alpaca.markets/")
    assert settings.peter_lynch_analysis_ttl_days == 90
    assert settings.alpaca_execution_enabled is False
    serialized = json.dumps(settings.redacted())
    assert "paper-key-id" not in serialized
    assert "paper-secret-key" not in serialized


def test_alpaca_reconnect_maximum_cannot_be_below_initial_delay() -> None:
    with pytest.raises(ValidationError, match="maximum reconnect"):
        AppSettings(
            _env_file=None,
            alpaca_stream_reconnect_initial_seconds=10,
            alpaca_stream_reconnect_max_seconds=5,
        )


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
    assert settings.sec_document_max_filings == 3
    assert settings.sec_document_max_bytes == 350_000
    assert settings.sec_document_max_snippets == 5


def test_sec_filing_lookback_accepts_ninety_days() -> None:
    settings = AppSettings(
        _env_file=None,
        sec_filing_lookback_days=90,
    )

    assert settings.sec_filing_lookback_days == 90

    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, sec_filing_lookback_days=91)


def test_peter_lynch_analysis_ttl_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, peter_lynch_analysis_ttl_days=0)
