"""Typed process configuration and safe diagnostic rendering."""

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyUrl, Field, HttpUrl, SecretStr, model_validator
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
    alert_checkpoint_interval_seconds: int = Field(default=30, ge=5, le=300)
    definition_path: Path = Path("configs/marketbot/7.47.0.yaml")
    entry_confirmation_rule_version: Literal["2.0.0", "3.0.0", "4.0.0", "5.0.0"] | None = None
    nats_url: SecretStr = SecretStr("nats://127.0.0.1:4222")
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_data_feed: Literal["iex", "sip", "delayed_sip", "boats", "overnight"] = "iex"
    alpaca_adjustment: Literal["raw", "split", "dividend", "all"] = "split"
    alpaca_rest_batch_size: int = Field(default=20, ge=1, le=100)
    alpaca_stream_handshake_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    alpaca_stream_reconnect_initial_seconds: float = Field(default=1.0, gt=0, le=60)
    alpaca_stream_reconnect_max_seconds: float = Field(default=300.0, ge=1, le=3600)
    alpaca_stream_stable_seconds: float = Field(default=60.0, gt=0, le=3600)
    alpaca_stream_recovery_buffer_bars: int = Field(default=100_000, ge=1000, le=1_000_000)
    microstructure_max_symbols: int = Field(default=40, ge=1, le=200)
    market_history_refresh_seconds: int = Field(default=3600, ge=60, le=86400)
    market_history_request_timeout_seconds: int = Field(default=1800, ge=10, le=3600)
    alpaca_data_base_url: HttpUrl = HttpUrl("https://data.alpaca.markets")
    alpaca_market_data_stream_url: AnyUrl = AnyUrl("wss://stream.data.alpaca.markets/v2")
    alpaca_options_data_base_url: HttpUrl = HttpUrl("https://data.alpaca.markets")
    alpaca_options_contracts_base_url: HttpUrl = HttpUrl("https://paper-api.alpaca.markets")
    alpaca_options_feed: Literal["opra", "indicative"] | None = None
    openai_api_key: SecretStr | None = None
    news_intelligence_model: str = "gpt-5.4-nano-2026-03-17"
    thesis_review_model: str = "gpt-5.4-nano-2026-03-17"
    news_intelligence_prompt_path: Path = Path("configs/rules/news_intelligence/1.0.0.yaml")
    news_intelligence_refresh_seconds: int = Field(default=300, ge=60, le=3600)
    news_intelligence_lookback_hours: int = Field(default=24, ge=1, le=168)
    news_intelligence_max_articles_per_cycle: int = Field(default=100, ge=1, le=500)
    options_gamma_refresh_seconds: int = Field(default=600, ge=60, le=3600)
    options_gamma_days_forward: int = Field(default=45, ge=1, le=180)
    options_gamma_strike_range_percent: Decimal = Field(
        default=Decimal("50"), ge=Decimal("10"), le=Decimal("200")
    )
    options_gamma_concurrency: int = Field(default=4, ge=1, le=20)
    alpaca_execution_enabled: Literal[False] = False
    universe_refresh_seconds: int = Field(default=120, ge=30, le=3600)
    rotation_interval_minutes: int = Field(default=5, ge=1, le=1440)
    sec_enabled: bool = False
    sec_user_agent: str | None = None
    sec_refresh_hours: int = Field(default=6, ge=1, le=168)
    sec_filing_lookback_days: int = Field(default=2, ge=1, le=90)
    sec_document_max_filings: int = Field(default=3, ge=0, le=10)
    sec_document_max_bytes: int = Field(default=350_000, ge=10_000, le=5_000_000)
    sec_document_max_snippets: int = Field(default=5, ge=1, le=20)
    sec_document_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    peter_lynch_analysis_ttl_days: int = Field(default=90, ge=1, le=365)

    @model_validator(mode="after")
    def validate_external_data(self) -> AppSettings:
        key_configured = bool(
            self.alpaca_api_key_id and self.alpaca_api_key_id.get_secret_value().strip()
        )
        secret_configured = bool(
            self.alpaca_api_secret_key and self.alpaca_api_secret_key.get_secret_value().strip()
        )
        if key_configured != secret_configured:
            raise ValueError("Alpaca API key and secret must be configured together")
        if self.alpaca_stream_reconnect_max_seconds < (
            self.alpaca_stream_reconnect_initial_seconds
        ):
            raise ValueError("Alpaca maximum reconnect delay cannot be below initial delay")
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
    def sec_configured(self) -> bool:
        value = self.sec_user_agent.strip() if self.sec_user_agent else ""
        return " " in value and "@" in value and "." in value.rsplit("@", 1)[-1]

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())

    def redacted(self) -> dict[str, Any]:
        """Return JSON-compatible diagnostics without exposing secret values."""
        return self.model_dump(mode="json")
