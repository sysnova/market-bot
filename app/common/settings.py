"""Typed process configuration and safe diagnostic rendering."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import SecretStr
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

    def redacted(self) -> dict[str, Any]:
        """Return JSON-compatible diagnostics without exposing secret values."""
        return self.model_dump(mode="json")
