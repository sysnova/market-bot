"""Redis contract: durable canonical data, bounded views and no duplicate payloads."""

import json

import fakeredis
import pytest
from redis.exceptions import ConnectionError

from app.integration.redis_ticker_cache import RedisTickerCache


def client(server: fakeredis.FakeServer) -> RedisTickerCache:
    return RedisTickerCache(fakeredis.FakeRedis(server=server, decode_responses=True))


def row(timestamp: int, close: int = 10) -> list[object]:
    return ["AAPL", "1Min", timestamp, True, json.dumps({"close": close})]


def test_distinct_processes_share_payloads_and_preserve_event_positions() -> None:
    server = fakeredis.FakeServer()
    first, second = client(server), client(server)
    a, b = first.view(2), second.view(2)
    first.call("add", a, [row(1), row(2)])
    second.call("add", b, [row(1), row(2)])
    assert first.call("stats")["unique_payloads"] == 1
    first.call("add", a, [row(2, 12), row(3, 13)])
    assert first.call("history", a, "AAPL", "1Min", None, True) == [
        json.dumps({"close": 12}),
        json.dumps({"close": 13}),
    ]
    assert second.call("history", b, "AAPL", "1Min", None, True) == [
        json.dumps({"close": 10}),
        json.dumps({"close": 10}),
    ]
    first.close()
    assert second.call("stats")["unique_payloads"] == 1


def test_canonical_history_survives_clients_and_bounds_retention() -> None:
    server = fakeredis.FakeServer()
    first = client(server)
    first.open_persistent("history:AAPL:1Min", 2)
    first.call("add", "history:AAPL:1Min", [row(1), row(2), row(3)])
    first.close()
    second = client(server)
    assert len(second.call("history", "history:AAPL:1Min", "AAPL", "1Min", None, True)) == 2
    second.call("add", "history:AAPL:1Min", [row(3, 15)])
    assert second.call("history", "history:AAPL:1Min", "AAPL", "1Min", 1, True) == [
        json.dumps({"close": 15})
    ]


def test_contexts_and_failed_connection_do_not_fall_back_to_python_cache() -> None:
    server = fakeredis.FakeServer()
    first, second = client(server), client(server)
    first.open_persistent("context:test", 10)
    first.call("put", "context:test", "AAPL", '{"score":42}')
    first.close()
    assert second.call("get", "context:test", "AAPL") == '{"score":42}'
    server.connected = False
    with pytest.raises(ConnectionError):
        second.call("get", "context:test", "AAPL")


def test_typed_contexts_resume_by_explicit_scope_without_colliding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.common import context_cache
    from app.contracts import AnalysisHorizon, MarketBar
    from app.integration.tests.test_market_bar_store import bar

    server = fakeredis.FakeServer()
    first = client(server)
    monkeypatch.setattr(context_cache, "_backend", first)
    values = context_cache.grouped_context_store(AnalysisHorizon, MarketBar, scope="consumer-a")
    expected = bar(minute=0, close="100")
    values["AAPL"][AnalysisHorizon.SWING] = expected
    first.close()
    second = client(server)
    monkeypatch.setattr(context_cache, "_backend", second)
    resumed = context_cache.grouped_context_store(AnalysisHorizon, MarketBar, scope="consumer-a")
    assert resumed.get("AAPL")[AnalysisHorizon.SWING] == expected
    other = context_cache.grouped_context_store(AnalysisHorizon, MarketBar, scope="consumer-b")
    assert other.get("AAPL") is None
