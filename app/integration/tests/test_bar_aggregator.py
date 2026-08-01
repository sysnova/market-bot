from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar
from app.integration.bar_aggregator import MinuteBarAggregator

START = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)


def minute_bar(offset: int, *, close: str, volume: str = "10") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=START + timedelta(minutes=offset),
        open=price - Decimal("0.1"),
        high=price + Decimal("0.2"),
        low=price - Decimal("0.2"),
        close=price,
        volume=Decimal(volume),
        trade_count=2,
        vwap=price,
        source="alpaca",
        feed="sip",
    )


def test_aggregator_emits_completed_five_and_fifteen_minute_bars() -> None:
    aggregator = MinuteBarAggregator(
        targets=(BarTimeframe.MINUTE_5, BarTimeframe.MINUTE_15)
    )
    emitted: list[MarketBar] = []
    for minute in range(16):
        emitted.extend(aggregator.add(minute_bar(minute, close=str(100 + minute))))

    five = tuple(item for item in emitted if item.timeframe is BarTimeframe.MINUTE_5)
    fifteen = tuple(item for item in emitted if item.timeframe is BarTimeframe.MINUTE_15)
    assert len(five) == 3
    assert len(fifteen) == 1
    assert fifteen[0].timestamp == START
    assert fifteen[0].open == Decimal("99.9")
    assert fifteen[0].close == Decimal("114")
    assert fifteen[0].high == Decimal("114.2")
    assert fifteen[0].low == Decimal("99.8")
    assert fifteen[0].volume == Decimal("150")
    assert fifteen[0].trade_count == 30
    assert fifteen[0].is_final is True


def test_aggregator_ignores_provider_updates_and_rejects_non_minute_input() -> None:
    aggregator = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_5,))
    updating = minute_bar(0, close="100").model_copy(update={"is_final": False})

    assert aggregator.add(updating) == ()
    daily = minute_bar(0, close="100").model_copy(
        update={"timeframe": BarTimeframe.DAY_1}
    )
    try:
        aggregator.add(daily)
    except ValueError as error:
        assert "1Min" in str(error)
    else:
        raise AssertionError("expected non-minute input to fail")


def test_aggregator_emits_completed_hourly_bar_for_patreon_caps() -> None:
    aggregator = MinuteBarAggregator(targets=(BarTimeframe.HOUR_1,))
    emitted: list[MarketBar] = []
    for minute in range(91):
        emitted.extend(aggregator.add(minute_bar(minute, close=str(100 + minute))))

    assert len(emitted) == 1
    assert emitted[0].timeframe is BarTimeframe.HOUR_1
    assert emitted[0].timestamp == START.replace(minute=0) + timedelta(hours=1)
    assert emitted[0].close == Decimal("189")
