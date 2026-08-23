from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, MarketBar
from app.swing_engine import SwingContext, SwingEngineV9, SwingEngineV10


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
        symbol="ASTS",
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


def _context() -> SwingContext:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    daily = [
        _bar(
            BarTimeframe.DAY_1,
            start + timedelta(days=index),
            open_="100",
            high="101",
            low="99",
            close="100",
        )
        for index in range(55)
    ]
    daily.extend(
        (
            _bar(
                BarTimeframe.DAY_1,
                start + timedelta(days=55),
                open_="100",
                high="100",
                low="90",
                close="92",
            ),
            _bar(
                BarTimeframe.DAY_1,
                start + timedelta(days=56),
                open_="92",
                high="92",
                low="77",
                close="78",
            ),
            _bar(
                BarTimeframe.DAY_1,
                start + timedelta(days=57),
                open_="78",
                high="80",
                low="73",
                close="75",
            ),
            _bar(
                BarTimeframe.DAY_1,
                start + timedelta(days=58),
                open_="75",
                high="79",
                low="74",
                close="77",
            ),
            _bar(
                BarTimeframe.DAY_1,
                start + timedelta(days=59),
                open_="77",
                high="80",
                low="75",
                close="77.5",
            ),
        )
    )
    prior = datetime(2026, 6, 8, 13, 30, tzinfo=UTC)
    intraday = [
        _bar(
            BarTimeframe.MINUTE_15,
            prior + timedelta(minutes=15 * index),
            open_="75",
            high="75.5",
            low="74.5",
            close="75",
        )
        for index in range(21)
    ]
    session = datetime(2026, 6, 9, 13, 30, tzinfo=UTC)
    intraday.extend(
        (
            _bar(
                BarTimeframe.MINUTE_15,
                session,
                open_="75",
                high="76",
                low="74",
                close="75.5",
            ),
            _bar(
                BarTimeframe.MINUTE_15,
                session + timedelta(minutes=15),
                open_="75.5",
                high="76.5",
                low="75",
                close="76",
            ),
            _bar(
                BarTimeframe.MINUTE_15,
                session + timedelta(minutes=30),
                open_="76",
                high="77.2",
                low="75.5",
                close="76.8",
            ),
            _bar(
                BarTimeframe.MINUTE_15,
                session + timedelta(minutes=45),
                open_="76.8",
                high="78.2",
                low="76.2",
                close="78",
                vwap="77",
            ),
        )
    )
    return SwingContext(
        symbol="ASTS",
        as_of=intraday[-1].timestamp,
        price=intraday[-1].close,
        daily_bars=tuple(daily),
        intraday_bars=tuple(intraday),
    )


@pytest.mark.unit
def test_v10_rearms_after_a_multisession_selloff_and_uses_the_correction_low() -> None:
    context = _context()
    metrics: dict[str, object] = {
        "failed_breakout": False,
        "atr14": Decimal("10"),
        "daily_sma20": Decimal("90"),
        "resistance": Decimal("100"),
        "risk_flags": ("broken_daily_structure",),
        "structure_broken_confirmed": True,
    }

    assert SwingEngineV9()._recovery_assessment(context, metrics) is None

    recovery = SwingEngineV10()._recovery_assessment(context, metrics)

    assert recovery is not None
    invalidation, _, risk_percent, risk_atr, reward_risk, pivot = recovery
    assert pivot.low == Decimal("73")
    assert invalidation == Decimal("72.0000")
    assert risk_percent < Decimal("8")
    assert risk_atr < Decimal("1")
    assert reward_risk > Decimal("3")


@pytest.mark.unit
def test_v10_selloff_lookback_must_cover_more_than_one_session() -> None:
    assert SwingEngineV10()._recovery_maximum_risk_percent == Decimal("12")
    with pytest.raises(ValueError, match="recovery_selloff_lookback_days"):
        SwingEngineV10(recovery_selloff_lookback_days=1)
