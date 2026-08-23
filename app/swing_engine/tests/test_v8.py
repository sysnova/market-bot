from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import AnalysisVerdict, BarTimeframe, MarketBar
from app.swing_engine import SwingContext, SwingEngineV8


def _bar(
    symbol: str,
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
        symbol=symbol,
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


def _daily_bars() -> tuple[MarketBar, ...]:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    bars = [
        _bar(
            "TEST",
            BarTimeframe.DAY_1,
            start + timedelta(days=index),
            open_="100",
            high="101",
            low="99",
            close="100",
        )
        for index in range(56)
    ]
    bars.extend(
        (
            _bar(
                "TEST",
                BarTimeframe.DAY_1,
                start + timedelta(days=56),
                open_="100",
                high="101",
                low="99",
                close="100",
            ),
            _bar(
                "TEST",
                BarTimeframe.DAY_1,
                start + timedelta(days=57),
                open_="97",
                high="97",
                low="90",
                close="94",
            ),
            _bar(
                "TEST",
                BarTimeframe.DAY_1,
                start + timedelta(days=58),
                open_="94",
                high="97",
                low="91.5",
                close="95",
            ),
            _bar(
                "TEST",
                BarTimeframe.DAY_1,
                start + timedelta(days=59),
                open_="95",
                high="98",
                low="92",
                close="96",
            ),
        )
    )
    return tuple(bars)


def _intraday_bars(*, confirmed: bool) -> tuple[MarketBar, ...]:
    prior_start = datetime(2026, 6, 8, 13, 30, tzinfo=UTC)
    bars = [
        _bar(
            "TEST",
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
        (
            "95.6",
            "97",
            "95.5",
            "96.5" if confirmed else "95.8",
            "96.2",
        ),
    )
    bars.extend(
        _bar(
            "TEST",
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


def _context(*, confirmed: bool) -> SwingContext:
    intraday = _intraday_bars(confirmed=confirmed)
    return SwingContext(
        symbol="TEST",
        as_of=intraday[-1].timestamp,
        price=intraday[-1].close,
        daily_bars=_daily_bars(),
        intraday_bars=intraday,
    )


@pytest.mark.unit
def test_v8_confirms_structure_recovery_with_tactical_invalidation() -> None:
    result = SwingEngineV8().analyze(_context(confirmed=True))
    metrics = {item.name: item.value for item in result.metrics}

    assert result.engine_version == "8.0.0"
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["classification"] == "recovery"
    assert metrics["entry_lane"] == "STRUCTURE_RECOVERY"
    assert metrics["swing_entry_gate_passed"] is True
    assert metrics["recovery_entry_gate_passed"] is True
    assert metrics["invalidation_source"] == "intraday_recovery_low"
    assert metrics["structural_invalidation"] < metrics["invalidation"] < Decimal("96.5")
    assert metrics["reward_risk_to_resistance"] >= Decimal("1.5")


@pytest.mark.unit
def test_v8_does_not_confirm_recovery_without_intraday_breakout() -> None:
    result = SwingEngineV8().analyze(_context(confirmed=False))
    metrics = {item.name: item.value for item in result.metrics}

    assert result.verdict is not AnalysisVerdict.FAVORABLE
    assert metrics["entry_lane"] == "NONE"
    assert metrics["recovery_entry_gate_passed"] is False
    assert metrics["swing_entry_gate_passed"] is False


@pytest.mark.unit
def test_v8_recovery_thresholds_must_be_positive() -> None:
    with pytest.raises(ValueError, match="recovery_maximum_risk_percent"):
        SwingEngineV8(recovery_maximum_risk_percent=Decimal("0"))
