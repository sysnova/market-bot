from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import AnalysisVerdict, BarTimeframe, MarketBar
from app.swing_engine import SwingContext, SwingEngineV5

AS_OF = datetime(2026, 8, 13, 21, tzinfo=UTC)


def _bar(
    index: int,
    *,
    close: Decimal,
    high: Decimal | None = None,
    low: Decimal | None = None,
    vwap: Decimal | None = None,
    timeframe: BarTimeframe = BarTimeframe.DAY_1,
) -> MarketBar:
    spacing = timedelta(days=1) if timeframe is BarTimeframe.DAY_1 else timedelta(minutes=15)
    return MarketBar(
        symbol="TEST",
        timeframe=timeframe,
        timestamp=AS_OF - spacing * (80 - index),
        open=close - Decimal("0.10"),
        high=high if high is not None else close + Decimal("0.50"),
        low=low if low is not None else close - Decimal("0.50"),
        close=close,
        volume=Decimal("1000000"),
        vwap=vwap,
        source="fixture",
        feed="fixture",
    )


def _context(daily: list[MarketBar], *, price: Decimal) -> SwingContext:
    intraday = [
        _bar(
            index,
            close=price - Decimal("0.20") + Decimal(index) / Decimal("100"),
            timeframe=BarTimeframe.MINUTE_15,
        )
        for index in range(40, 80)
    ]
    return SwingContext(
        symbol="TEST",
        as_of=AS_OF,
        price=price,
        daily_bars=tuple(daily),
        intraday_bars=tuple(intraday),
    )


@pytest.mark.unit
def test_v5_uses_real_recent_low_for_invalidation_not_pivot_avwap() -> None:
    daily = [
        _bar(
            index,
            close=Decimal("90") + Decimal(index) * Decimal("0.25"),
            vwap=Decimal("109"),
        )
        for index in range(80)
    ]
    daily[-5] = daily[-5].model_copy(
        update={"low": Decimal("95"), "vwap": Decimal("109")}
    )

    result = SwingEngineV5().analyze(_context(daily, price=Decimal("110")))
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["pivot_low_avwap"] > Decimal("108")
    assert metrics["structural_support"] == Decimal("95.0000")
    assert metrics["invalidation"] == Decimal("93.5750")
    assert metrics["invalidation_source"] == "recent_daily_low"


@pytest.mark.unit
def test_v5_uses_closes_for_resistance_and_keeps_wick_as_liquidity_only() -> None:
    daily = [
        _bar(index, close=Decimal("100") + Decimal(index) * Decimal("0.10"))
        for index in range(80)
    ]
    daily[-6] = daily[-6].model_copy(
        update={
            "open": Decimal("106"),
            "close": Decimal("110"),
            "high": Decimal("130"),
            "low": Decimal("105.50"),
        }
    )

    result = SwingEngineV5().analyze(_context(daily, price=Decimal("108")))
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["resistance"] == Decimal("110.0000")
    assert metrics["liquidity_high"] == Decimal("130.0000")
    assert metrics["resistance_source"] == "completed_daily_closes"
    assert metrics["liquidity_high"] != metrics["resistance"]


@pytest.mark.unit
def test_v5_failed_breakout_blocks_favorable_pullback_until_close_reclaims_level() -> None:
    daily = [
        _bar(index, close=Decimal("100") + Decimal(index) * Decimal("0.02"))
        for index in range(80)
    ]
    daily[-5] = daily[-5].model_copy(
        update={"open": Decimal("101.4"), "high": Decimal("104"), "close": Decimal("103")}
    )
    for offset, close in ((-4, "101.20"), (-3, "101.10"), (-2, "101.30"), (-1, "101.40")):
        daily[offset] = daily[offset].model_copy(
            update={
                "open": Decimal(close) - Decimal("0.10"),
                "high": Decimal(close) + Decimal("0.30"),
                "low": Decimal(close) - Decimal("0.30"),
                "close": Decimal(close),
            }
        )

    result = SwingEngineV5().analyze(_context(daily, price=Decimal("101.40")))
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["failed_breakout"] is True
    assert metrics["failed_breakout_level"] is not None
    assert metrics["swing_entry_gate_passed"] is False
    assert result.verdict is AnalysisVerdict.WATCH
    assert "failed_breakout_recovery_pending" in result.reasons
