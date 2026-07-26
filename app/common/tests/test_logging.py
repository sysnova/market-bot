import json
import logging

import pytest

from app.common.logging import bind_context, clear_context, configure_logging, get_logger


def test_logging_emits_structured_json_and_bound_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    clear_context()
    bind_context(correlation_id="corr-123")

    get_logger("test").info("order_received", symbol="AAPL")

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "order_received"
    assert payload["symbol"] == "AAPL"
    assert payload["correlation_id"] == "corr-123"
    assert payload["level"] == "info"


def test_configure_logging_sets_standard_library_level() -> None:
    configure_logging(level="WARNING", json_output=False)

    assert logging.getLogger().level == logging.WARNING
