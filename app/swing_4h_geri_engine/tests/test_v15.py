from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.contracts import (
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportZonePosition,
)
from app.swing_4h_geri_engine import (
    Swing4HGeriContext,
    Swing4HGeriEngineV14,
    Swing4HGeriEngineV15,
    Swing4HGeriEngineV16,
)
from app.swing_4h_geri_engine.tests.test_geri_engine import level_three_bars


def _support(occurred_at: datetime) -> SupportAssessment:
    return SupportAssessment(
        symbol="AAPL",
        occurred_at=occurred_at,
        engine_version="0.2.0",
        state=SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=SupportConfirmationType.BASE_BREAKOUT,
        current_price=Decimal("93.2"),
        zone_low=Decimal("92"),
        zone_center=Decimal("93"),
        zone_high=Decimal("94"),
        invalidation=Decimal("90"),
        support_score=Decimal("90"),
        reaction_score=Decimal("82"),
        reversal_score=Decimal("72"),
        confidence=Decimal("0.90"),
        support_sources=("pivot_daily_20", "weekly_sma10"),
        reasons=("fixture",),
        context_hash=f"sha256:{'7' * 64}",
    )


def test_v15_enriches_matching_main_long_zone_without_promoting_maturity() -> None:
    bars = level_three_bars()
    as_of = bars[-1].timestamp
    base_context = Swing4HGeriContext(
        symbol="AAPL",
        bars=bars,
        current_price=Decimal("93.2"),
        as_of=as_of,
        current_price_at=as_of,
    )
    context = replace(base_context, support=_support(bars[-1].timestamp))

    native = Swing4HGeriEngineV14().analyze(base_context)
    enriched = Swing4HGeriEngineV15().analyze(context)
    metrics = {item.name: item.value for item in enriched.metrics}

    assert enriched.maturity is native.maturity
    assert metrics["support_contribution"] == "STRUCTURE"
    assert metrics["support_zone_match"] == "MAIN"
    assert "support_confirmation_structure_confluence" in enriched.reasons


def test_v16_keeps_liquidity_sweep_as_context_on_matching_geri_zone() -> None:
    bars = level_three_bars()
    as_of = bars[-1].timestamp
    support = _support(as_of).model_copy(
        update={
            "engine_version": "0.3.0",
            "state": SupportState.LIQUIDITY_SWEEP,
            "confirmation_type": SupportConfirmationType.NONE,
            "reversal_score": Decimal("35"),
            "zone_position": SupportZonePosition.IN_ZONE,
            "zone_distance_atr": Decimal("0"),
            "zone_distance_percent": Decimal("0"),
            "touch_count": 2,
            "touch_age_sessions": 0,
            "actionability_score": Decimal("60"),
        }
    )
    context = Swing4HGeriContext(
        symbol="AAPL",
        bars=bars,
        current_price=Decimal("93.2"),
        as_of=as_of,
        current_price_at=as_of,
        support=support,
    )

    result = Swing4HGeriEngineV16().analyze(context)
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["support_contribution"] == "CONTEXT"
    assert metrics["support_zone_match"] == "MAIN"
