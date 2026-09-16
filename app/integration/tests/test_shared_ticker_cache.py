from app.integration.shared_ticker_cache import TickerCache


def row(timestamp: float, payload: str = "same", final: bool = True) -> list[object]:
    return ["AAPL", "1Day", timestamp, final, payload]


def test_engines_share_payloads_but_not_event_progress_or_capacity() -> None:
    cache = TickerCache()
    cache.open("one", "long", 3)
    cache.open("two", "swing", 1)
    cache.add("long", [row(1), row(2, "next")])
    cache.add("swing", [row(1)])
    assert cache.history("swing", "AAPL", "1Day", None, False) == ["same"]
    assert cache.stats()["unique_payloads"] == 2
    cache.add("swing", [row(2, "next")])
    assert cache.history("long", "AAPL", "1Day", None, False) == ["same", "next"]
    cache.release("one")
    assert cache.stats()["unique_payloads"] == 1
    cache.release("two")
    assert cache.stats()["unique_payloads"] == 0


def test_corrections_final_filter_and_out_of_order_bars_are_isolated() -> None:
    cache = TickerCache()
    cache.open("one", "a", 2)
    cache.open("two", "b", 2)
    cache.add("a", [row(2, "two"), row(1)])
    cache.add("b", [row(2, "two")])
    cache.add("a", [row(2, "forming", False), row(0, "old")])
    assert cache.history("a", "AAPL", "1Day", 1, True) == ["same"]
    assert cache.history("b", "AAPL", "1Day", None, False) == ["two"]
    assert cache.stats()["unique_payloads"] == 3


def test_contexts_are_deduplicated_and_dead_owners_are_reaped() -> None:
    cache = TickerCache()
    cache.open("one", "a", 1)
    cache.open("two", "b", 1)
    cache.put("a", "AAPL", "support")
    cache.put("b", "AAPL", "support")
    assert cache.stats()["unique_payloads"] == 1
    cache.put("a", "AAPL", "new support")
    assert cache.get("b", "AAPL") == "support"
    cache.remove("a", "AAPL")
    cache.expire(float("inf"))
    assert cache.stats()["unique_payloads"] == 0
