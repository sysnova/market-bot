"""Configuration loading without putting credentials in source code."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 suite
    import tomli as tomllib

from .errors import AlpacaConfigurationError

_ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_FEEDS = {"iex", "sip", "delayed_sip", "boats", "overnight"}
_ADJUSTMENTS = {"raw", "split", "dividend", "all"}


@dataclass(frozen=True, slots=True)
class AlpacaConfig:
    """Validated settings for Alpaca's read-only Stock Market Data API."""

    api_key_id: str
    api_secret_key: str
    feed: str = "iex"
    adjustment: str = "all"
    data_base_url: str = "https://data.alpaca.markets"
    timeout_seconds: float = 30.0
    max_retries: int = 3
    symbols_per_request: int = 100
    max_pages: int = 1000

    def __post_init__(self) -> None:
        if not self.api_key_id.strip() or not self.api_secret_key.strip():
            raise AlpacaConfigurationError("Alpaca API key ID and secret key cannot be blank")
        if self.feed not in _FEEDS:
            raise AlpacaConfigurationError(
                f"unsupported Alpaca feed {self.feed!r}; expected one of {sorted(_FEEDS)}"
            )
        if self.adjustment not in _ADJUSTMENTS:
            raise AlpacaConfigurationError(
                "unsupported Alpaca adjustment; expected raw, split, dividend or all"
            )
        parsed_url = urlsplit(self.data_base_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "data.alpaca.markets":
            raise AlpacaConfigurationError(
                "data_base_url must be Alpaca's HTTPS Stock Market Data endpoint"
            )
        if self.timeout_seconds <= 0:
            raise AlpacaConfigurationError("timeout_seconds must be positive")
        if not 0 <= self.max_retries <= 10:
            raise AlpacaConfigurationError("max_retries must be between 0 and 10")
        if not 1 <= self.symbols_per_request <= 1000:
            raise AlpacaConfigurationError("symbols_per_request must be between 1 and 1000")
        if not 1 <= self.max_pages <= 10_000:
            raise AlpacaConfigurationError("max_pages must be between 1 and 10000")

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> AlpacaConfig:
        """Load ``[alpaca]`` settings, resolving exact ``${ENV_VAR}`` values."""

        config_path = Path(path)
        try:
            with config_path.open("rb") as config_file:
                document = tomllib.load(config_file)
        except FileNotFoundError as error:
            raise AlpacaConfigurationError(
                f"Alpaca configuration file was not found: {config_path}"
            ) from error
        except tomllib.TOMLDecodeError as error:
            raise AlpacaConfigurationError(
                f"Alpaca configuration file is not valid TOML: {config_path}"
            ) from error

        section = document.get("alpaca")
        if not isinstance(section, dict):
            raise AlpacaConfigurationError("configuration must contain an [alpaca] section")
        environment = os.environ if environ is None else environ
        key_id = _required_secret(section, "api_key_id", environment)
        secret_key = _required_secret(section, "api_secret_key", environment)

        return cls(
            api_key_id=key_id,
            api_secret_key=secret_key,
            feed=_string(section, "feed", "iex"),
            adjustment=_string(section, "adjustment", "all"),
            data_base_url=_string(
                section, "data_base_url", "https://data.alpaca.markets"
            ),
            timeout_seconds=_number(section, "timeout_seconds", 30.0),
            max_retries=_integer(section, "max_retries", 3),
            symbols_per_request=_integer(section, "symbols_per_request", 100),
            max_pages=_integer(section, "max_pages", 1000),
        )


def _required_secret(
    section: dict[str, Any], key: str, environ: Mapping[str, str]
) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AlpacaConfigurationError(f"[alpaca].{key} must be a non-blank string")
    cleaned = value.strip()
    placeholder = _ENV_PLACEHOLDER.fullmatch(cleaned)
    if placeholder is None:
        return cleaned
    variable = placeholder.group(1)
    resolved = environ.get(variable, "").strip()
    if not resolved:
        raise AlpacaConfigurationError(
            f"environment variable {variable} required by [alpaca].{key} is missing"
        )
    return resolved


def _string(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise AlpacaConfigurationError(f"[alpaca].{key} must be a string")
    return value.strip()


def _number(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AlpacaConfigurationError(f"[alpaca].{key} must be a number")
    return float(value)


def _integer(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlpacaConfigurationError(f"[alpaca].{key} must be an integer")
    return value
