"""Typed process configuration and safe diagnostic rendering."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import AnyUrl, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AppSettings(BaseSettings):
    """Infrastructure settings loaded from ``MARKETBOT_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MARKETBOT_",
        extra="forbid",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    database_url: SecretStr = SecretStr("postgresql://marketbot:marketbot@localhost:5432/marketbot")
    nats_url: SecretStr = SecretStr("nats://localhost:4222")
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_data_feed: Literal["iex", "sip", "delayed_sip", "boats", "overnight"] = "iex"
    alpaca_data_base_url: HttpUrl = HttpUrl("https://data.alpaca.markets")
    alpaca_market_data_stream_url: AnyUrl = AnyUrl("wss://stream.data.alpaca.markets/v2")
    alpaca_trading_base_url: HttpUrl = HttpUrl("https://paper-api.alpaca.markets")
    alpaca_trade_updates_stream_url: AnyUrl = AnyUrl("wss://paper-api.alpaca.markets/stream")
    alpaca_paper: bool = True

    @model_validator(mode="after")
    def validate_alpaca(self) -> AppSettings:
        key_configured = bool(
            self.alpaca_api_key_id and self.alpaca_api_key_id.get_secret_value().strip()
        )
        secret_configured = bool(
            self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value().strip()
        )
        if key_configured != secret_configured:
            raise ValueError("Alpaca API key and secret must be configured together")
        if self.alpaca_paper and self.alpaca_trading_base_url.host != "paper-api.alpaca.markets":
            raise ValueError("Alpaca paper mode requires the paper-api.alpaca.markets endpoint")
        return self

    @property
    def alpaca_configured(self) -> bool:
        return bool(
            self.alpaca_api_key_id
            and self.alpaca_api_key_id.get_secret_value().strip()
            and self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value().strip()
        )

    def redacted(self) -> dict[str, Any]:
        """Return JSON-compatible diagnostics without exposing secret values."""
        return self.model_dump(mode="json")
