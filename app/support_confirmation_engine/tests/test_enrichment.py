from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import (
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportZonePosition,
)
from app.support_confirmation_engine import classify_support_enrichment

NOW = datetime(2026, 8, 24, 14, tzinfo=UTC)


def _support(state: SupportState, **updates: object) -> SupportAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW,
        "engine_version": "0.3.0",
        "state": state,
        "confirmation_type": SupportConfirmationType.NONE,
        "current_price": Decimal("101"),
        "zone_low": Decimal("99"),
        "zone_center": Decimal("100"),
        "zone_high": Decimal("101"),
        "invalidation": Decimal("96"),
        "support_score": Decimal("80"),
        "reaction_score": Decimal("75"),
        "reversal_score": Decimal("35"),
        "confidence": Decimal("0.65"),
        "zone_position": SupportZonePosition.IN_ZONE,
        "zone_distance_atr": Decimal("0"),
        "zone_distance_percent": Decimal("0"),
        "touch_count": 2,
        "touch_age_sessions": 0,
        "actionability_score": Decimal("60"),
        "support_sources": ("pivot_daily_20", "weekly_sma10"),
        "reasons": ("fixture",),
        "context_hash": f"sha256:{'a' * 64}",
    }
    values.update(updates)
    return SupportAssessment.model_validate(values)


def test_liquidity_sweep_is_pending_context_until_reclaimed() -> None:
    sweep = _support(SupportState.LIQUIDITY_SWEEP)
    reclaimed = _support(
        SupportState.RECLAIMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        four_hour_reclaim=True,
    )

    assert classify_support_enrichment(sweep, current_price=Decimal("101")) == "CONTEXT"
    assert classify_support_enrichment(reclaimed, current_price=Decimal("101")) == "REACTION"


def test_b_wave_reaction_is_retained_as_context_without_confirming_swing() -> None:
    reaction = _support(
        SupportState.RECLAIMED,
        confirmation_type=SupportConfirmationType.SWEEP_RECLAIM,
        b_wave_risk=True,
    )

    assert classify_support_enrichment(reaction, current_price=Decimal("101")) == "CONTEXT"


def test_stale_or_below_zone_reaction_does_not_enrich_swing() -> None:
    stale = _support(SupportState.RECLAIMED, touch_age_sessions=6)
    below = _support(
        SupportState.RECLAIMED,
        current_price=Decimal("98"),
        zone_position=SupportZonePosition.BELOW_ZONE,
    )

    assert classify_support_enrichment(stale, current_price=Decimal("101")) is None
    assert classify_support_enrichment(below, current_price=Decimal("98")) is None


def test_structure_requires_actionability_and_reasonable_distance() -> None:
    valid = _support(
        SupportState.STRUCTURE_CONFIRMED,
        confirmation_type=SupportConfirmationType.BASE_BREAKOUT,
        reversal_score=Decimal("70"),
        actionability_score=Decimal("72"),
        zone_position=SupportZonePosition.ABOVE_ZONE,
        zone_distance_atr=Decimal("1.2"),
    )
    extended = valid.model_copy(update={"zone_distance_atr": Decimal("1.6")})
    weak = valid.model_copy(update={"actionability_score": Decimal("54.9")})

    assert classify_support_enrichment(valid, current_price=Decimal("101")) == "STRUCTURE"
    assert classify_support_enrichment(extended, current_price=Decimal("101")) is None
    assert classify_support_enrichment(weak, current_price=Decimal("101")) is None
