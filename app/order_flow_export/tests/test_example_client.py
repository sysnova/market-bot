import json
from typing import Any

import pytest

from app.order_flow_export import example_client as client


def test_readable_state_preserves_zero_and_shows_unknown_quote() -> None:
    data: dict[str, Any] = {
        "event_type": "order-flow.state.assessed",
        "payload": {
            "symbol": "ASTS", "occurred_at": "2026-09-15T15:00:00Z",
            "state": "NEUTRAL", "current_price": "123.4500",
            "cumulative_delta": "0", "confidence": "0", "quote_fresh": False,
            "windows": [{"window_seconds": 15, "buy_volume": "100", "sell_volume": "100",
                         "delta": "0"}],
        },
    }
    rendered = client.format_message(data)
    for expected in ("ASTS", "NEUTRAL", "123.4500", "CVD=0", "confianza=0",
                     "quote=sin cotizacion fresca", "15s", "buy=100", "sell=100", "delta=0"):
        assert expected in rendered
    assert client.format_message(data, raw_json=True) == json.dumps(data, ensure_ascii=False)


def test_subscription_and_transition_are_identifiable() -> None:
    assert "Suscripto: ASTS, NBIS" in client.format_message({
        "type": "subscribed", "symbols": ["ASTS", "NBIS"], "delivery": "live",
    })
    message = client.format_message({
        "event_type": "order-flow.state.transitioned",
        "payload": {"symbol": "ASTS", "previous_state": "NEUTRAL", "state": "BUY_PRESSURE"},
    })
    assert "CAMBIO" in message
    assert "NEUTRAL -> BUY_PRESSURE" in message


def test_token_from_environment_or_hidden_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETBOT_ORDER_FLOW_WS_TOKEN", "test-env-secret")
    monkeypatch.setattr(client, "getpass", lambda _prompt: pytest.fail("unexpected prompt"))
    assert client.read_token() == "test-env-secret"
    monkeypatch.delenv("MARKETBOT_ORDER_FLOW_WS_TOKEN")
    monkeypatch.setattr(client, "getpass", lambda _prompt: "test-prompt-secret")
    assert client.read_token() == "test-prompt-secret"
    monkeypatch.setattr(client, "getpass", lambda _prompt: " ")
    with pytest.raises(ValueError, match="token"):
        client.read_token()
