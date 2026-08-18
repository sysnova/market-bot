from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, MarketBar
from app.swing_engine import SwingContext, SwingEngineV6
from app.swing_engine.v6 import (
    FailedBreakoutAssessment,
    FailedBreakoutState,
    _failed_breakout_lifecycle,
)

AS_OF = datetime(2026, 8, 17, 21, tzinfo=UTC)


def _bar(
    index: int,
    *,
    close: str = "100",
    high: str | None = None,
    low: str | None = None,
) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        symbol="TEST",
        timeframe=BarTimeframe.DAY_1,
        timestamp=AS_OF + timedelta(days=index),
        open=price - Decimal("0.10"),
        high=Decimal(high) if high is not None else price + Decimal("0.50"),
        low=Decimal(low) if low is not None else price - Decimal("0.50"),
        close=price,
        volume=Decimal("1000000"),
        vwap=price,
        source="fixture",
        feed="fixture",
    )


def _old_failed_breakout(*, count: int = 80) -> list[MarketBar]:
    bars = [_bar(index) for index in range(count)]
    bars[40] = _bar(40, close="101", high="101.20", low="99.80")
    bars[41] = _bar(41, close="100", high="100.40", low="99.70")
    return bars


def _assessment(
    bars: list[MarketBar],
    *,
    maximum_age_days: int = 60,
    reset_atr_multiple: Decimal = Decimal("5"),
) -> FailedBreakoutAssessment:
    return _failed_breakout_lifecycle(
        tuple(bars),
        resistance_lookback_days=20,
        failure_window_days=5,
        maximum_age_days=maximum_age_days,
        structural_reset_lookback_days=20,
        reset_atr_multiple=reset_atr_multiple,
    )


def _context(bars: list[MarketBar]) -> SwingContext:
    price = bars[-1].close
    intraday = tuple(
        _bar(index, close=str(price)).model_copy(
            update={
                "timeframe": BarTimeframe.MINUTE_15,
                "timestamp": AS_OF + timedelta(minutes=15 * index),
            }
        )
        for index in range(40)
    )
    return SwingContext(
        symbol="TEST",
        as_of=bars[-1].timestamp,
        price=price,
        daily_bars=tuple(bars),
        intraday_bars=intraday,
    )


@pytest.mark.unit
def test_v6_keeps_recent_unresolved_failed_breakout_active() -> None:
    assessment = _assessment(_old_failed_breakout(count=45))

    assert assessment.state is FailedBreakoutState.ACTIVE
    assert assessment.blocks_entry is True
    assert assessment.breakout_at == AS_OF + timedelta(days=40)
    assert assessment.failure_at == AS_OF + timedelta(days=41)
    assert assessment.atr14_snapshot is not None


@pytest.mark.unit
def test_v6_structural_reset_uses_prior_twenty_bars_excluding_current() -> None:
    bars = _old_failed_breakout(count=46)
    bars[45] = _bar(45, close="98.90", high="99.20", low="98.50")

    assessment = _assessment(bars, reset_atr_multiple=Decimal("100"))

    assert assessment.state is FailedBreakoutState.STRUCTURE_INVALIDATED
    assert assessment.blocks_entry is False
    assert assessment.resolved_at == bars[45].timestamp


@pytest.mark.unit
def test_v6_uses_breakout_atr_snapshot_for_volatility_reset() -> None:
    bars = _old_failed_breakout(count=46)
    bars[25] = _bar(25, close="100", high="100.50", low="90")
    bars[42] = _bar(42, close="100", high="106", low="96")
    bars[43] = _bar(43, close="100", high="107", low="95")
    bars[44] = _bar(44, close="100", high="108", low="94")
    bars[45] = _bar(45, close="95", high="96", low="94")

    assessment = _assessment(bars)

    assert assessment.atr14_snapshot == Decimal("1.0286")
    assert assessment.state is FailedBreakoutState.VOLATILITY_INVALIDATED
    assert assessment.blocks_entry is False


@pytest.mark.unit
def test_v6_price_invalidation_precedes_expiry_on_same_bar() -> None:
    bars = _old_failed_breakout(count=46)
    bars[45] = _bar(45, close="94", high="95", low="93")

    assessment = _assessment(bars, maximum_age_days=5)

    assert assessment.state is FailedBreakoutState.STRUCTURE_INVALIDATED
    assert assessment.age_bars == 5


@pytest.mark.unit
def test_v6_expires_event_after_configured_completed_bar_age() -> None:
    assessment = _assessment(_old_failed_breakout(count=51), maximum_age_days=10)

    assert assessment.state is FailedBreakoutState.EXPIRED
    assert assessment.blocks_entry is False
    assert assessment.age_bars == 10


@pytest.mark.unit
def test_v6_recovery_of_original_level_is_auditable() -> None:
    bars = _old_failed_breakout(count=45)
    bars[44] = _bar(44, close="100.90", high="101.10", low="100.40")

    assessment = _assessment(bars)

    assert assessment.state is FailedBreakoutState.RECOVERED
    assert assessment.blocks_entry is False
    assert assessment.resolved_at == bars[44].timestamp


def _lower_base_after_old_failure(*, confirmed: bool) -> list[MarketBar]:
    count = 71 if confirmed else 68
    bars = [_bar(index, close="120") for index in range(count)]
    bars[25] = _bar(25, close="120", high="120.50", low="90")
    bars[40] = _bar(40, close="121", high="121.20", low="119.80")
    bars[41] = _bar(41, close="100", high="100.40", low="99.70")
    for index in range(42, count):
        bars[index] = _bar(index)
    bars[65] = _bar(65, close="101", high="101.20", low="99.80")
    for index in range(66, count):
        bars[index] = _bar(index, close="100.80", high="101.10", low="100.20")
    return bars


@pytest.mark.unit
def test_v6_new_breakout_remains_pending_without_lookahead() -> None:
    assessment = _assessment(
        _lower_base_after_old_failure(confirmed=False),
        maximum_age_days=100,
        reset_atr_multiple=Decimal("100"),
    )

    assert assessment.state is FailedBreakoutState.NEW_BREAKOUT_PENDING
    assert assessment.blocks_entry is True
    assert assessment.superseding_breakout_at == AS_OF + timedelta(days=65)


@pytest.mark.unit
def test_v6_confirmed_new_base_supersedes_old_failure_without_ghost_reactivation() -> None:
    assessment = _assessment(
        _lower_base_after_old_failure(confirmed=True),
        maximum_age_days=100,
        reset_atr_multiple=Decimal("100"),
    )

    assert assessment.state is FailedBreakoutState.SUPERSEDED
    assert assessment.blocks_entry is False
    assert assessment.superseding_breakout_at == AS_OF + timedelta(days=65)
    assert assessment.resolved_at == AS_OF + timedelta(days=70)


@pytest.mark.unit
def test_v6_analysis_emits_terminal_state_without_retaining_failed_breakout_veto() -> None:
    bars = _old_failed_breakout(count=80)
    bars[45] = _bar(45, close="98.90", high="99.20", low="98.50")

    result = SwingEngineV6(
        failed_breakout_reset_atr_multiple=Decimal("100")
    ).analyze(_context(bars))
    metrics = {item.name: item.value for item in result.metrics}

    assert result.engine_version == "6.0.0"
    assert metrics["failed_breakout"] is False
    assert metrics["failed_breakout_state"] == "STRUCTURE_INVALIDATED"
    assert metrics["failed_breakout_reset_reason"] == "STRUCTURE_INVALIDATED"
    assert "failed_breakout" not in metrics["risk_flags"]
    assert "failed_breakout_structure_invalidated" in result.reasons
