from pathlib import Path

import pytest

from alpaca_dataframe_connector import AlpacaConfig, AlpacaConfigurationError


def test_loads_credentials_from_environment_placeholders(tmp_path: Path) -> None:
    config_path = tmp_path / "alpaca.toml"
    config_path.write_text(
        """
[alpaca]
api_key_id = "${TEST_ALPACA_KEY}"
api_secret_key = "${TEST_ALPACA_SECRET}"
feed = "sip"
adjustment = "all"
timeout_seconds = 12.5
max_retries = 4
symbols_per_request = 25
max_pages = 500
""".strip(),
        encoding="utf-8",
    )

    config = AlpacaConfig.from_toml(
        config_path,
        environ={"TEST_ALPACA_KEY": "key", "TEST_ALPACA_SECRET": "secret"},
    )

    assert config.api_key_id == "key"
    assert config.api_secret_key == "secret"
    assert config.feed == "sip"
    assert config.adjustment == "all"
    assert config.timeout_seconds == 12.5
    assert config.max_retries == 4
    assert config.symbols_per_request == 25
    assert config.max_pages == 500


def test_missing_secret_environment_variable_is_actionable(tmp_path: Path) -> None:
    config_path = tmp_path / "alpaca.toml"
    config_path.write_text(
        """
[alpaca]
api_key_id = "${MISSING_KEY}"
api_secret_key = "secret"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(AlpacaConfigurationError, match="MISSING_KEY"):
        AlpacaConfig.from_toml(config_path, environ={})


@pytest.mark.parametrize("feed", ["unknown", " "])
def test_rejects_unsupported_feed(feed: str) -> None:
    with pytest.raises(AlpacaConfigurationError, match="feed"):
        AlpacaConfig(api_key_id="key", api_secret_key="secret", feed=feed)
