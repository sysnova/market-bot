from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    BarTimeframe,
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    GeriStructuralLevel,
    MarketBar,
    NamedValue,
    SwingTradeAssessment,
    SwingTradeMaturity,
    TradeSide,
)
from app.swing_trade_engine import SwingTradeContext, SwingTradeEngine

START = datetime(2026, 5, 1, 20, tzinfo=UTC)


def daily_bars(*, support: Decimal = Decimal("95"), count: int = 60) -> tuple[MarketBar, ...]:
    values: list[MarketBar] = []
    for index in range(count):
        low = Decimal("100") + Decimal(index) / Decimal("10")
        high = low + Decimal("4")
        if index == 0:
            low, high = Decimal("80"), Decimal("85")
        if index == count - 21:
            low, high = Decimal("110"), Decimal("120")
        if index >= count - 20:
            low = support if index == count - 20 else max(support, Decimal("98"))
            high = Decimal("119") if index == count - 1 else Decimal("112")
        close = (low + high) / Decimal("2")
        values.append(
            MarketBar(
                symbol="AAPL",
                timeframe=BarTimeframe.DAY_1,
                timestamp=START + timedelta(days=index),
                open=close - Decimal("1"),
                high=high,
                low=low,
                close=close,
                volume=Decimal("1000"),
                source="test",
                feed="sip",
                is_final=True,
            )
        )
    return tuple(values)


def geri(bars: tuple[MarketBar, ...]) -> GeriAssessment:
    at = bars[-1].timestamp
    levels = (
        GeriStructuralLevel(
            sequence=1,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("90"),
            source_at=at - timedelta(days=3),
            confirmed_at=at - timedelta(days=3),
            broken_at=at - timedelta(days=2),
        ),
        GeriStructuralLevel(
            sequence=2,
            kind=GeriLevelKind.RESISTANCE,
            price=Decimal("105"),
            source_at=at - timedelta(days=2),
            confirmed_at=at - timedelta(days=2),
            broken_at=at - timedelta(days=1),
        ),
        GeriStructuralLevel(
            sequence=3,
            kind=GeriLevelKind.SUPPORT,
            price=Decimal("97"),
            source_at=at - timedelta(days=1),
            confirmed_at=at - timedelta(days=1),
        ),
    )
    return GeriAssessment(
        symbol="AAPL",
        occurred_at=at,
        engine_version="1.2.0",
        maturity=GeriMaturity.IN_ZONE_4H,
        current_price=Decimal("97"),
        levels=levels,
        active_level_sequence=3,
        active_level_kind=GeriLevelKind.SUPPORT,
        active_level_price=Decimal("97"),
        atr14=Decimal("4"),
        breakout_buffer=Decimal("0.4"),
        zone_low=Decimal("96"),
        zone_high=Decimal("98"),
        invalidation=Decimal("95"),
        trade_side=TradeSide.LONG,
        standalone_swing=True,
        reasons=("manual_monitor_only",),
        context_hash=f"sha256:{'a' * 64}",
    )


def analyze(
    price: str, *, support: str = "95", with_geri: bool = False
) -> SwingTradeAssessment:
    bars = daily_bars(support=Decimal(support))
    return SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal(price),
            daily_bars=bars,
            geri=geri(bars) if with_geri else None,
        )
    )


def test_engine_calculates_fibonacci_support_and_primary_rr() -> None:
    result = analyze("97")

    assert result.impulse_low == Decimal("80.0000")
    assert result.impulse_high == Decimal("120.0000")
    assert result.fibonacci_50 == Decimal("100.0000")
    assert result.fibonacci_618 == Decimal("95.2800")
    assert result.fibonacci_1618 == Decimal("144.7200")
    assert result.support_20d == Decimal("95.0000")
    assert result.resistance_20d == Decimal("119.0000")
    assert result.primary_target == result.resistance_20d
    assert result.reward_risk > Decimal("1.5")


@pytest.mark.parametrize("lookback", [60, 70, 80])
def test_engine_supports_parameterized_completed_session_windows(lookback: int) -> None:
    bars = daily_bars(count=lookback)
    result = SwingTradeEngine(fibonacci_lookback_sessions=lookback).analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal("97"),
            daily_bars=bars,
        )
    )
    assert result.impulse_low == Decimal("80.0000")
    assert result.impulse_high == Decimal("120.0000")


def test_engine_ignores_unfinished_daily_bar_without_lookahead() -> None:
    bars = daily_bars()
    unfinished = bars[-1].model_copy(
        update={
            "timestamp": bars[-1].timestamp + timedelta(days=1),
            "high": Decimal("200"),
            "low": Decimal("10"),
            "is_final": False,
        }
    )
    result = SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=unfinished.timestamp,
            current_price=Decimal("97"),
            daily_bars=(*bars, unfinished),
        )
    )

    assert result.impulse_low == Decimal("80.0000")
    assert result.impulse_high == Decimal("120.0000")


def test_engine_matures_st1_through_st4_hierarchically() -> None:
    assert analyze("96", support="90").maturity is SwingTradeMaturity.ST1
    assert analyze("100.1").maturity is SwingTradeMaturity.ST2
    assert analyze("97").maturity is SwingTradeMaturity.ST3
    assert analyze("97", with_geri=True).maturity is SwingTradeMaturity.ST4


def test_st4_accepts_v13_structural_long_and_ignores_its_tactical_lane() -> None:
    bars = daily_bars()
    v13 = geri(bars).model_copy(
        update={
            "engine_version": "1.3.0",
            "metrics": (
                NamedValue(name="countertrend_side", value=TradeSide.SHORT),
                NamedValue(name="countertrend_state", value=GeriMaturity.L4),
            ),
        }
    )

    result = SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=v13,
        )
    )

    assert result.maturity is SwingTradeMaturity.ST4
    assert result.geri_assessment_id == v13.assessment_id


def test_v13_tactical_long_cannot_promote_st4_over_structural_short() -> None:
    bars = daily_bars()
    v13 = geri(bars).model_copy(
        update={
            "engine_version": "1.3.0",
            "trade_side": TradeSide.SHORT,
            "metrics": (
                NamedValue(name="countertrend_side", value=TradeSide.LONG),
                NamedValue(name="countertrend_state", value=GeriMaturity.L4),
            ),
        }
    )

    result = SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=v13,
        )
    )

    assert result.maturity is SwingTradeMaturity.ST3
    assert result.geri_confluence is False


def test_engine_requires_strictly_more_than_minimum_rr() -> None:
    base = analyze("97")
    exact = SwingTradeEngine(minimum_reward_risk=base.reward_risk).analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=base.occurred_at,
            current_price=Decimal("97"),
            daily_bars=daily_bars(),
        )
    )
    assert exact.maturity is None
    assert "insufficient_reward_risk" in exact.reasons


def test_maximum_distance_to_zone_is_inclusive_then_rejects_above_boundary() -> None:
    base = analyze("100.1")
    exact_ratio = (base.current_price - base.zone_high) / base.atr14
    at_boundary = SwingTradeEngine(maximum_distance_to_zone_atr=exact_ratio).analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=base.occurred_at,
            current_price=base.current_price,
            daily_bars=daily_bars(),
        )
    )
    outside = SwingTradeEngine(
        maximum_distance_to_zone_atr=exact_ratio - Decimal("0.0001")
    ).analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=base.occurred_at,
            current_price=base.current_price,
            daily_bars=daily_bars(),
        )
    )

    assert at_boundary.maturity is SwingTradeMaturity.ST2
    assert outside.maturity is None
    assert "too_far_from_fibonacci_zone" in outside.reasons


def test_engine_rejects_long_impulse_when_global_high_precedes_low() -> None:
    bars = list(daily_bars())
    first = bars[0]
    bars[0] = first.model_copy(update={"low": Decimal("100"), "high": Decimal("130")})
    last = bars[-1]
    bars[-1] = last.model_copy(update={"low": Decimal("70"), "high": Decimal("119")})

    with pytest.raises(ValueError, match="low before high"):
        SwingTradeEngine().analyze(
            SwingTradeContext(
                symbol="AAPL",
                as_of=last.timestamp + timedelta(minutes=15),
                current_price=Decimal("97"),
                daily_bars=tuple(bars),
            )
        )


def test_st4_rejects_stale_geri() -> None:
    bars = daily_bars()
    stale = geri(bars).model_copy(update={"occurred_at": bars[-4].timestamp})
    result = SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=stale,
        )
    )
    assert result.maturity is SwingTradeMaturity.ST3
    assert result.geri_confluence is False


@pytest.mark.parametrize(
    "updates",
    [
        {"trade_side": TradeSide.SHORT},
        {"maturity": GeriMaturity.INVALIDATED},
        {"zone_low": Decimal("90"), "zone_high": Decimal("92")},
        {"standalone_swing": False},
    ],
)
def test_st4_rejects_wrong_side_invalidated_nonoverlap_or_nonstandalone_geri(
    updates: dict[str, object],
) -> None:
    bars = daily_bars()
    rejected = geri(bars).model_copy(update=updates)
    result = SwingTradeEngine().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp + timedelta(minutes=15),
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=rejected,
        )
    )

    assert result.maturity is SwingTradeMaturity.ST3
    assert result.geri_confluence is False
