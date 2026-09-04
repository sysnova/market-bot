from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, MarketBar
from app.swing_trade_engine import SwingTradeContext
from app.swing_trade_engine.recovery_quality import calculate_macd, macd_metrics
from app.swing_trade_engine.tests.test_engine import _confirmation_bars, daily_bars
from app.swing_trade_engine.v14 import SwingTradeEngineV14
from app.swing_trade_engine.v16 import SwingTradeEngineV16


@pytest.mark.parametrize(
    "recovering,expected",
    [
        (False, "LOCAL_BREAKOUT"),
        (True, "RECOVERY_WITH_MOMENTUM"),
    ],
)
def test_breakout_quality_uses_4h_slope_without_changing_native_entry(
    recovering: bool,
    expected: str,
) -> None:
    daily = daily_bars()
    confirmations = _confirmation_bars(daily)
    touched = confirmations[-2].model_copy(
        update={
            "open": Decimal("96.8"),
            "high": Decimal("96.9"),
        }
    )
    before = touched.model_copy(update={"timestamp": touched.timestamp - timedelta(minutes=15)})
    confirmations = (*confirmations[:-2], before, touched, confirmations[-1])
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)
    closes = (*(Decimal("100"),) * 35, *(Decimal(100 - 2 * i) for i in range(1, 11)))
    if recovering:
        closes = (*closes, Decimal("80"), Decimal("81"))
    last_start = daily[-1].timestamp.replace(hour=17, minute=30)
    four_hour = tuple(
        daily[-1].model_copy(
            update={
                "timeframe": BarTimeframe.HOUR_4,
                "timestamp": last_start - timedelta(days=len(closes) - i - 1),
                "open": close,
                "close": close,
                "low": close - 1,
                "high": close + 1,
            }
        )
        for i, close in enumerate(closes)
    )
    context = SwingTradeContext(
        symbol="AAPL",
        as_of=as_of,
        current_price=Decimal("97"),
        current_price_at=as_of,
        daily_bars=daily,
        confirmation_bars=confirmations,
        four_hour_bars=four_hour,
    )
    native = SwingTradeEngineV14().analyze(context)
    observed = SwingTradeEngineV16().analyze(context)
    assert observed.maturity == native.maturity
    assert observed.maturity is not None and observed.maturity.value == "ST3"
    assert observed.invalidation == native.invalidation
    assert observed.primary_target == native.primary_target
    metrics = {m.name: m.value for m in observed.metrics}
    assert metrics["recovery_quality"] == expected
    assert metrics["macd_4h_status"] == "AVAILABLE"
    assert isinstance(metrics["macd_4h_histogram"], Decimal)
    assert metrics["macd_4h_histogram"] < 0


def test_macd_flat_series_and_minimum_history() -> None:
    assert calculate_macd((Decimal("100"),) * 34) is None
    result = calculate_macd((Decimal("100"),) * 35)
    assert result is not None
    assert result.line == result.signal == result.histogram == result.previous_histogram == 0


def test_negative_histogram_can_be_improving() -> None:
    closes = (
        *(Decimal("100"),) * 35,
        *(Decimal(100 - 2 * i) for i in range(1, 11)),
        Decimal("80"),
        Decimal("81"),
    )
    result = calculate_macd(closes)
    assert result is not None
    assert result.histogram < 0
    assert result.histogram > result.previous_histogram


def test_macd_matches_hand_calculated_short_periods() -> None:
    result = calculate_macd(tuple(map(Decimal, (1, 2, 3, 6, 5))), fast=2, slow=3, signal=2)
    assert result is not None
    assert abs(result.histogram - Decimal(-2) / Decimal(27)) < Decimal("1e-20")


def test_daily_macd_ignores_incomplete_and_future_bars() -> None:
    bars = daily_bars()
    at = bars[-1].timestamp + timedelta(days=1)
    expected = macd_metrics(
        bars, symbol="AAPL", timeframe=BarTimeframe.DAY_1, as_of=at, prefix="daily"
    )
    future = bars[-1].model_copy(
        update={"timestamp": at + timedelta(days=1), "close": Decimal("999")}
    )
    unfinished = future.model_copy(update={"timestamp": at, "is_final": False})
    actual = macd_metrics(
        (*bars, unfinished, future),
        symbol="AAPL",
        timeframe=BarTimeframe.DAY_1,
        as_of=at,
        prefix="daily",
    )
    assert actual == expected


def test_short_afternoon_four_hour_bar_is_available_only_at_session_close() -> None:
    bars = tuple(
        MarketBar(
            symbol="AAPL",
            timeframe=BarTimeframe.HOUR_4,
            timestamp=datetime(2026, 7, 1, 17, 30, tzinfo=UTC) + timedelta(days=i),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            source="fixture",
            feed="sip",
            is_final=True,
        )
        for i in range(35)
    )
    end = bars[-1].timestamp + timedelta(hours=2, minutes=30)
    before = dict(
        (m.name, m.value)
        for m in macd_metrics(
            bars,
            symbol="AAPL",
            timeframe=BarTimeframe.HOUR_4,
            as_of=end - timedelta(seconds=1),
            prefix="4h",
        )
    )
    after = dict(
        (m.name, m.value)
        for m in macd_metrics(
            bars, symbol="AAPL", timeframe=BarTimeframe.HOUR_4, as_of=end, prefix="4h"
        )
    )
    assert before["macd_4h_status"] == "INSUFFICIENT_HISTORY"
    assert after["macd_4h_status"] == "AVAILABLE"


def test_stale_macd_is_unknown_not_bearish() -> None:
    bars = daily_bars()
    metrics = {
        m.name: m.value
        for m in macd_metrics(
            bars,
            symbol="AAPL",
            timeframe=BarTimeframe.DAY_1,
            as_of=bars[-1].timestamp + timedelta(days=10),
            prefix="daily",
        )
    }
    assert metrics["macd_daily_status"] == "STALE"
    assert metrics["macd_daily_histogram"] is None
    assert metrics["macd_daily_direction"] == "UNKNOWN"


@pytest.mark.parametrize("volume", ["1000", "2000"])
def test_v16_preserves_native_decision_and_geometry_without_four_hour_data(volume: str) -> None:
    daily = daily_bars()
    confirmations = _confirmation_bars(daily, current_volume=volume)
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)
    context = SwingTradeContext(
        symbol="AAPL",
        as_of=as_of,
        current_price=Decimal("97"),
        current_price_at=as_of,
        daily_bars=daily,
        confirmation_bars=confirmations,
    )
    native = SwingTradeEngineV14().analyze(context)
    observed = SwingTradeEngineV16().analyze(context)
    for field in [
        "maturity",
        "eligible",
        "zone_low",
        "zone_high",
        "invalidation",
        "primary_target",
        "reward_risk",
    ]:
        assert getattr(observed, field) == getattr(native, field)
    metrics = {m.name: m.value for m in observed.metrics}
    assert metrics["recovery_quality_mode"] == "OBSERVATION"
    assert metrics["macd_4h_status"] == "INSUFFICIENT_HISTORY"
    assert metrics["recovery_quality"] == ("EARLY_REACTION" if volume == "2000" else "WATCHING")
    future = daily[-1].model_copy(
        update={"timeframe": BarTimeframe.HOUR_4, "timestamp": as_of + timedelta(days=1)}
    )
    with_future = SwingTradeEngineV16().analyze(replace(context, four_hour_bars=(future,)))
    assert with_future.context_hash == observed.context_hash
