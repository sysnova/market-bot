from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import AnalysisVerdict, BarTimeframe, MarketBar
from app.swing_engine import SwingContext, SwingEngineV8, SwingEngineV9


def _bar(
    timeframe: BarTimeframe,
    timestamp: datetime,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    vwap: str | None = None,
) -> MarketBar:
    return MarketBar(
        symbol="TEST",
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100000"),
        vwap=Decimal(vwap or close),
        source="fixture",
        feed="fixture",
    )


def _daily_bars_with_stale_confirmed_pivot() -> tuple[MarketBar, ...]:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    bars = [
        _bar(
            BarTimeframe.DAY_1,
            start + timedelta(days=index),
            open_="100",
            high="101",
            low="99",
            close="100",
        )
        for index in range(60)
    ]
    bars[50] = _bar(
        BarTimeframe.DAY_1,
        start + timedelta(days=50),
        open_="100",
        high="101",
        low="98",
        close="100",
    )
    bars[58] = _bar(
        BarTimeframe.DAY_1,
        start + timedelta(days=58),
        open_="97",
        high="97",
        low="90",
        close="94",
        vwap="92",
    )
    bars[59] = _bar(
        BarTimeframe.DAY_1,
        start + timedelta(days=59),
        open_="94",
        high="98",
        low="91.5",
        close="95",
        vwap="95",
    )
    return tuple(bars)


def _intraday_bars() -> tuple[MarketBar, ...]:
    prior_start = datetime(2026, 6, 8, 13, 30, tzinfo=UTC)
    bars = [
        _bar(
            BarTimeframe.MINUTE_15,
            prior_start + timedelta(minutes=15 * index),
            open_="94",
            high="94.5",
            low="93.5",
            close="94",
        )
        for index in range(21)
    ]
    session_start = datetime(2026, 6, 9, 13, 30, tzinfo=UTC)
    session = (
        ("94", "95", "93.8", "94.6", "94.5"),
        ("94.6", "95.5", "94.5", "95", "94.9"),
        ("95", "96", "95", "95.6", "95.5"),
        ("95.6", "97", "95.5", "96.5", "96.2"),
    )
    bars.extend(
        _bar(
            BarTimeframe.MINUTE_15,
            session_start + timedelta(minutes=15 * index),
            open_=open_,
            high=high,
            low=low,
            close=close,
            vwap=vwap,
        )
        for index, (open_, high, low, close, vwap) in enumerate(session)
    )
    return tuple(bars)


@pytest.mark.unit
def test_v9_recovery_avwap_anchors_to_the_recent_correction() -> None:
    intraday = _intraday_bars()
    daily = _daily_bars_with_stale_confirmed_pivot()
    context = SwingContext(
        symbol="TEST",
        as_of=intraday[-1].timestamp,
        price=intraday[-1].close,
        daily_bars=daily,
        intraday_bars=intraday,
    )
    legacy = SwingEngineV8().analyze(context)
    legacy_metrics = {item.name: item.value for item in legacy.metrics}
    result = SwingEngineV9().analyze(context)
    metrics = {item.name: item.value for item in result.metrics}

    assert legacy_metrics["price_vs_pivot_low_avwap_percent"] < 0
    assert legacy_metrics["entry_lane"] == "NONE"
    assert metrics["price_vs_pivot_low_avwap_percent"] < 0
    assert result.engine_version == "9.0.0"
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["entry_lane"] == "STRUCTURE_RECOVERY"
    assert metrics["recovery_pivot_at"] == daily[58].timestamp
    assert metrics["recovery_avwap_anchor_at"] == daily[58].timestamp
    assert metrics["recovery_avwap"] == Decimal("93.5000")
    assert metrics["price_vs_recovery_avwap_percent"] > 0
    assert metrics["recovery_avwap_gate_passed"] is True
    assert "recovery_avwap_reclaimed" in result.reasons
    assert "pivot_low_avwap_reclaimed" not in result.reasons
