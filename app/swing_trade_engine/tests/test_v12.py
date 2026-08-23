from decimal import Decimal

from app.contracts import (
    GeriMaturity,
    NamedValue,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportZonePosition,
    SwingTradeMaturity,
    TradeSide,
)
from app.swing_trade_engine import SwingTradeContext, SwingTradeEngineV12, SwingTradeEngineV13
from app.swing_trade_engine.tests.test_engine import _confirmation_bars, daily_bars, geri


def test_v12_uses_reacted_geri_countertrend_long_for_st4() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + (
        confirmations[-1].timestamp - confirmations[-2].timestamp
    )
    tactical = geri(bars).model_copy(
        update={
            "engine_version": "1.5.0",
            "trade_side": TradeSide.SHORT,
            "metrics": (
                NamedValue(name="countertrend_side", value=TradeSide.LONG),
                NamedValue(name="countertrend_state", value=GeriMaturity.L2_4H),
                NamedValue(name="countertrend_zone_low", value=Decimal("96")),
                NamedValue(name="countertrend_zone_high", value=Decimal("98")),
                NamedValue(name="countertrend_eligible", value=True),
                NamedValue(name="countertrend_expired", value=False),
            ),
        }
    )

    result = SwingTradeEngineV12().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            geri=tactical,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert result.maturity is SwingTradeMaturity.ST4
    assert result.geri_confluence is True
    assert result.geri_zone_low == Decimal("96")
    assert metrics["geri_zone_source"] == "COUNTERTREND"
    assert "geri_countertrend_reaction_confirmed" in result.reasons


def test_v12_adds_support_only_when_it_is_incremental_and_overlapping() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + (
        confirmations[-1].timestamp - confirmations[-2].timestamp
    )
    support = SupportAssessment(
        symbol="AAPL",
        occurred_at=bars[-1].timestamp,
        engine_version="0.2.0",
        state=SupportState.REACTION_CONFIRMED,
        confirmation_type=SupportConfirmationType.V_RECOVERY,
        current_price=Decimal("97"),
        zone_low=Decimal("95"),
        zone_center=Decimal("97"),
        zone_high=Decimal("99"),
        invalidation=Decimal("93"),
        support_score=Decimal("80"),
        reaction_score=Decimal("75"),
        reversal_score=Decimal("30"),
        confidence=Decimal("0.75"),
        support_sources=("fib_0618", "round_number"),
        reasons=("fixture",),
        context_hash=f"sha256:{'6' * 64}",
    )

    result = SwingTradeEngineV12().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            support=support,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert result.maturity is SwingTradeMaturity.ST3
    assert metrics["support_contribution"] == "REACTION"
    assert "support_confirmation_reaction_confluence" in result.reasons


def test_v13_keeps_liquidity_sweep_pending_on_the_native_swing_trade_zone() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + (
        confirmations[-1].timestamp - confirmations[-2].timestamp
    )
    support = SupportAssessment(
        symbol="AAPL",
        occurred_at=bars[-1].timestamp,
        engine_version="0.3.0",
        state=SupportState.LIQUIDITY_SWEEP,
        current_price=Decimal("97"),
        zone_low=Decimal("95"),
        zone_center=Decimal("97"),
        zone_high=Decimal("99"),
        invalidation=Decimal("93"),
        support_score=Decimal("80"),
        reaction_score=Decimal("75"),
        reversal_score=Decimal("30"),
        confidence=Decimal("0.60"),
        zone_position=SupportZonePosition.IN_ZONE,
        zone_distance_atr=Decimal("0"),
        zone_distance_percent=Decimal("0"),
        touch_count=2,
        touch_age_sessions=0,
        actionability_score=Decimal("60"),
        support_sources=("pivot_daily_20", "weekly_sma10"),
        reasons=("fixture",),
        context_hash=f"sha256:{'5' * 64}",
    )

    result = SwingTradeEngineV13().analyze(
        SwingTradeContext(
            symbol="AAPL",
            as_of=as_of,
            current_price=Decimal("97"),
            daily_bars=bars,
            support=support,
            confirmation_bars=confirmations,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["support_contribution"] == "CONTEXT"
    assert metrics["support_zone_match"] == "SWING_TRADE_ZONE"
