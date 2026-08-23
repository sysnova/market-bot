from decimal import Decimal

from app.contracts import (
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportZonePosition,
)
from app.swing_engine import SwingEngineV12
from app.swing_engine.tests.test_v11 import _bullish_context, _support


def _v03_pending_support() -> SupportAssessment:
    context = _bullish_context()
    return _support(context, SupportState.LIQUIDITY_SWEEP).model_copy(
        update={
            "engine_version": "0.3.0",
            "confirmation_type": SupportConfirmationType.NONE,
            "zone_position": SupportZonePosition.IN_ZONE,
            "zone_distance_atr": Decimal("0"),
            "zone_distance_percent": Decimal("0"),
            "touch_count": 2,
            "touch_age_sessions": 0,
            "actionability_score": Decimal("60"),
        }
    )


def test_v12_keeps_pending_sweep_as_context_on_matching_native_zone() -> None:
    base = _bullish_context()
    support = _v03_pending_support()
    result = SwingEngineV12().analyze(base.model_copy(update={"support": support}))
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["support_contribution"] == "CONTEXT"
    assert metrics["support_zone_match"] == "ENTRY_ZONE"
    assert metrics["support_actionability_score"] == Decimal("60")
    assert "support_confirmation_context_confluence" in result.reasons


def test_v12_does_not_attach_support_when_only_support_zone_matches_spot() -> None:
    base = _bullish_context()
    support = _v03_pending_support().model_copy(
        update={
            "zone_low": Decimal("70"),
            "zone_center": Decimal("70.5"),
            "zone_high": Decimal("71"),
            "invalidation": Decimal("68"),
        }
    )

    result = SwingEngineV12().analyze(base.model_copy(update={"support": support}))

    assert not any(item.name.startswith("support_") for item in result.metrics)
