from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, MarketBar
from app.patreon_caps_engine.indicators import (
    anchored_vwap,
    atr,
    confirmed_pivot_indices,
    fibonacci_levels,
    last_breakout_index,
    mean,
    relative_volume,
    rsi,
    sma,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(index: int, close: str, *, volume: str = "100") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        symbol="TEST",
        timeframe=BarTimeframe.DAY_1,
        timestamp=NOW + timedelta(days=index),
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal(volume),
        source="fixture",
        feed="test",
    )


def test_decimal_indicators_are_deterministic() -> None:
    bars = tuple(_bar(index, str(10 + index), volume=str(100 + index * 10)) for index in range(21))

    assert sma(tuple(bar.close for bar in bars), 20) == Decimal("20.5000")
    assert atr(bars, 14) == Decimal("2.0000")
    assert relative_volume(bars, 20) == Decimal("1.5385")
    assert anchored_vwap(bars, 0) > Decimal("20")


def test_pivots_and_fibonacci_require_a_confirmed_impulse() -> None:
    closes = ("12", "11", "8", "10", "12", "14", "16", "19", "17", "16")
    bars = tuple(_bar(index, close) for index, close in enumerate(closes))
    lows, highs = confirmed_pivot_indices(bars, radius=2)

    assert 2 in lows
    assert 7 in highs
    levels = fibonacci_levels(bars, atr14=Decimal("2"))
    assert levels is not None
    assert levels["fib_0_618"] < levels["fib_0_382"]


def test_indicator_guards_and_zero_volume_paths() -> None:
    bars = tuple(_bar(index, str(10 + index), volume="0") for index in range(21))

    with pytest.raises(ValueError, match="mean requires"):
        mean(())
    with pytest.raises(ValueError, match="SMA"):
        sma((Decimal("1"),), 2)
    with pytest.raises(ValueError, match="ATR"):
        atr(bars[:14], 14)
    with pytest.raises(ValueError, match="anchor_index"):
        anchored_vwap(bars, 21)
    with pytest.raises(ValueError, match="pivot radius"):
        confirmed_pivot_indices(bars, radius=0)

    assert relative_volume(bars[:20], 20) == 0
    assert relative_volume(bars, 20) == 0
    assert anchored_vwap(bars, 0) == Decimal("20.0000")


def test_breakout_fibonacci_and_rsi_negative_paths() -> None:
    flat = tuple(_bar(index, "10") for index in range(21))
    breakout = (*flat, _bar(21, "12"))

    assert last_breakout_index(flat[:20]) is None
    assert last_breakout_index(flat) is None
    assert last_breakout_index(breakout) == 21
    assert fibonacci_levels(flat, atr14=Decimal("2")) is None
    with pytest.raises(ValueError, match="RSI"):
        rsi((Decimal("1"),), period=2)
    assert rsi(tuple(Decimal(index) for index in range(15))) == Decimal("100")
    assert rsi((Decimal("1"),) * 15) == Decimal("50")
    assert rsi(tuple(Decimal(15 - index) for index in range(15))) == Decimal("0.0000")
