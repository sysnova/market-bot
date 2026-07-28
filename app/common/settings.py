"""Typed process configuration and safe diagnostic rendering."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import AnyUrl, Field, HttpUrl, SecretStr, field_validator, model_validator
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
    entry_watcher_enabled: bool = True
    entry_watch_ttl_days: int = Field(default=56, ge=7, le=365)
    nats_url: SecretStr = SecretStr("nats://127.0.0.1:4222")
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_data_feed: Literal["iex", "sip", "delayed_sip", "boats", "overnight"] = "iex"
    alpaca_adjustment: Literal["raw", "split", "dividend", "all"] = "split"
    alpaca_rest_batch_size: int = Field(default=20, ge=1, le=100)
    alpaca_data_base_url: HttpUrl = HttpUrl("https://data.alpaca.markets")
    alpaca_market_data_stream_url: AnyUrl = AnyUrl("wss://stream.data.alpaca.markets/v2")
    alpaca_watchlist: str = "AAPL,MSFT,NVDA,SPY,QQQ"
    alpaca_execution_enabled: Literal[False] = False
    supabase_url: HttpUrl | None = None
    supabase_desktop_api_key: SecretStr | None = None
    universe_refresh_seconds: int = Field(default=120, ge=30, le=3600)
    sec_enabled: bool = False
    sec_user_agent: str | None = None
    sec_refresh_hours: int = Field(default=6, ge=1, le=168)
    sec_filing_lookback_days: int = Field(default=2, ge=1, le=30)

    @field_validator("alpaca_watchlist")
    @classmethod
    def validate_alpaca_watchlist(cls, value: str) -> str:
        symbols = tuple(dict.fromkeys(item.strip().upper() for item in value.split(",")))
        if not symbols or any(
            not symbol
            or len(symbol) > 16
            or not all(character.isalnum() or character in ".-" for character in symbol)
            for symbol in symbols
        ):
            raise ValueError("Alpaca watchlist must contain comma-separated market symbols")
        return ",".join(symbols)

    @model_validator(mode="after")
    def validate_external_data(self) -> AppSettings:
        key_configured = bool(
            self.alpaca_api_key_id and self.alpaca_api_key_id.get_secret_value().strip()
        )
        secret_configured = bool(
            self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value().strip()
        )
        if key_configured != secret_configured:
            raise ValueError("Alpaca API key and secret must be configured together")
        supabase_url_configured = self.supabase_url is not None
        supabase_key_configured = bool(
            self.supabase_desktop_api_key
            and self.supabase_desktop_api_key.get_secret_value().strip()
        )
        if supabase_url_configured != supabase_key_configured:
            raise ValueError("Supabase URL and desktop API key must be configured together")
        if self.sec_enabled and not self.sec_configured:
            raise ValueError("SEC user agent must include an identifiable contact email")
        return self

    @property
    def alpaca_configured(self) -> bool:
        return bool(
            self.alpaca_api_key_id
            and self.alpaca_api_key_id.get_secret_value().strip()
            and self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value().strip()
        )

    @property
    def alpaca_symbols(self) -> tuple[str, ...]:
        return tuple(self.alpaca_watchlist.split(","))

    @property
    def sec_configured(self) -> bool:
        value = self.sec_user_agent.strip() if self.sec_user_agent else ""
        return " " in value and "@" in value and "." in value.rsplit("@", 1)[-1]

    @property
    def supabase_universe_configured(self) -> bool:
        return bool(
            self.supabase_url
            and self.supabase_desktop_api_key
            and self.supabase_desktop_api_key.get_secret_value().strip()
        )

    def redacted(self) -> dict[str, Any]:
        """Return JSON-compatible diagnostics without exposing secret values."""
        return self.model_dump(mode="json")
