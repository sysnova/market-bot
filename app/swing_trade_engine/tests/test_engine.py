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
from app.swing_trade_engine import SwingTradeContext, SwingTradeEngine, SwingTradeEngineV11
from app.swing_trade_engine.engine import _session_normalized_rvol

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


def _confirmation_bars(
    bars: tuple[MarketBar, ...], *, current_volume: str = "2000"
) -> tuple[MarketBar, ...]:
    session = bars[-1].timestamp + timedelta(days=1)
    history = tuple(
        MarketBar(
            symbol="AAPL",
            timeframe=BarTimeframe.MINUTE_15,
            timestamp=session - timedelta(days=20 - index) + timedelta(minutes=15),
            open=Decimal("98"),
            high=Decimal("99"),
            low=Decimal("97"),
            close=Decimal("98"),
            volume=Decimal("1000"),
            vwap=Decimal("98"),
            source="test",
            feed="sip",
            is_final=True,
        )
        for index in range(19)
    )
    touched = history[-1].model_copy(
        update={
            "timestamp": session,
            "open": Decimal("98"),
            "high": Decimal("99"),
            "low": Decimal("96"),
            "close": Decimal("96.5"),
            "volume": Decimal("1000"),
            "vwap": Decimal("97"),
        }
    )
    current = touched.model_copy(
        update={
            "timestamp": session + timedelta(minutes=15),
            "open": Decimal("96.6"),
            "high": Decimal("98"),
            "low": Decimal("96.2"),
            "close": Decimal("97"),
            "volume": Decimal(current_volume),
            "vwap": Decimal("96.8"),
        }
    )
    return (*history, touched, current)


def test_v11_keeps_st3_as_location_until_rejection_volume_and_vwap_confirm() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars, current_volume="1000")
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)

    result = SwingTradeEngineV11().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert result.maturity is SwingTradeMaturity.ST2
    assert metrics["entry_rejection_confirmed"] is True
    assert metrics["intraday_rvol_confirmed"] is False


def test_v11_promotes_st3_only_after_rejection_volume_and_vwap_confirm() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)

    result = SwingTradeEngineV11().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )

    assert result.maturity is SwingTradeMaturity.ST3


def test_v11_rejects_future_daily_or_geri_evidence() -> None:
    bars = daily_bars()
    as_of = bars[-2].timestamp

    with pytest.raises(ValueError, match="later than as_of"):
        SwingTradeEngineV11().analyze(
            SwingTradeContext(
                symbol="AAPL",
                as_of=as_of,
                current_price=Decimal("97"),
                daily_bars=bars,
                current_price_at=as_of,
            )
        )


def test_v11_requires_geri_reaction_before_st4() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)
    armed = geri(bars)
    reacted = armed.model_copy(
        update={
            "maturity": GeriMaturity.L2_4H,
            "fast_confirmation": True,
        }
    )

    location_only = SwingTradeEngineV11().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=armed,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )
    confirmed = SwingTradeEngineV11().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=reacted,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )

    assert location_only.maturity is SwingTradeMaturity.ST3
    assert confirmed.maturity is SwingTradeMaturity.ST4


def test_v11_rvol_compares_the_same_new_york_slot_across_dst() -> None:
    baseline = MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_15,
        timestamp=datetime(2026, 3, 6, 14, 45, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )
    current = baseline.model_copy(
        update={
            "timestamp": datetime(2026, 3, 9, 13, 45, tzinfo=UTC),
            "volume": Decimal("1500"),
        }
    )

    assert _session_normalized_rvol((baseline, current), minimum_samples=1) == Decimal(
        "1.5000"
    )
