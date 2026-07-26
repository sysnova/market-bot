from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar
from app.integration.market_bar_store import MarketBarStore

NOW = datetime(2026, 7, 26, 14, 30, tzinfo=UTC)


def bar(*, minute: int, close: str, final: bool = True) -> MarketBar:
    timestamp = NOW + timedelta(minutes=minute)
    price = Decimal(close)
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("100"),
        source="alpaca",
        feed="sip",
        is_final=final,
    )


def test_store_orders_bars_and_replaces_same_timestamp_with_provider_update() -> None:
    store = MarketBarStore(capacity_per_series=3)
    store.add(bar(minute=1, close="101"))
    store.add(bar(minute=0, close="100"))
    store.add(bar(minute=1, close="101.5", final=False))

    history = store.history("AAPL", BarTimeframe.MINUTE_1)

    assert tuple(item.close for item in history) == (Decimal("100"), Decimal("101.5"))
    assert history[-1].is_final is False


def test_store_applies_capacity_and_can_filter_updating_bars() -> None:
    store = MarketBarStore(capacity_per_series=2)
    for minute in range(3):
        store.add(bar(minute=minute, close=str(100 + minute), final=minute != 2))

    assert tuple(item.close for item in store.history("AAPL", BarTimeframe.MINUTE_1)) == (
        Decimal("101"),
        Decimal("102"),
    )
    assert tuple(
        item.close
        for item in store.history("AAPL", BarTimeframe.MINUTE_1, final_only=True)
    ) == (Decimal("101"),)


def test_store_keeps_symbol_and_timeframe_series_isolated() -> None:
    store = MarketBarStore()
    store.add(bar(minute=0, close="100"))

    assert store.history("MSFT", BarTimeframe.MINUTE_1) == ()
    assert store.history("AAPL", BarTimeframe.DAY_1) == ()
