from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    MacroRegime,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatreonCapsTransition,
    StrategyMode,
    new_uuid7,
)

NOW = datetime(2026, 8, 1, 15, 30, tzinfo=UTC)


def assessment() -> PatreonCapsAssessment:
    return PatreonCapsAssessment(
        symbol="NVO",
        occurred_at=NOW,
        rule_version="1.0.0",
        mode=StrategyMode.SHADOW,
        state=PatreonCapsState.SUPPORT_TEST,
        current_price=Decimal("48.50"),
        zone_low=Decimal("47.80"),
        zone_center=Decimal("48.20"),
        zone_high=Decimal("48.60"),
        invalidation=Decimal("46.70"),
        atr14=Decimal("1.20"),
        confluence_score=Decimal("78"),
        confirmation_score=Decimal("72"),
        alignment_score=Decimal("88"),
        patreon_score=Decimal("79.20"),
        macro_regime=MacroRegime.NEUTRAL,
        macro_threshold=Decimal("80"),
        support_sources=("pivot_daily", "sma_weekly", "avwap"),
        reasons=("support_test",),
    )


def test_assessment_validates_ordered_levels_and_scores() -> None:
    item = assessment()

    assert item.patreon_score == Decimal("79.20")
    with pytest.raises(ValidationError, match="levels"):
        PatreonCapsAssessment(**{**item.model_dump(), "zone_low": Decimal("49")})


def test_transition_links_one_state_change_and_optional_sizing() -> None:
    item = assessment()
    transition = PatreonCapsTransition(
        watch_id=new_uuid7(),
        symbol=item.symbol,
        previous_state=PatreonCapsState.SUPPORT_TEST,
        state=PatreonCapsState.CONFIRMED_V,
        occurred_at=NOW,
        rule_version="1.0.0",
        current_price=item.current_price,
        zone_low=item.zone_low,
        zone_center=item.zone_center,
        zone_high=item.zone_high,
        invalidation=item.invalidation,
        confluence_score=item.confluence_score,
        confirmation_score=item.confirmation_score,
        alignment_score=item.alignment_score,
        patreon_score=Decimal("82"),
        macro_regime=MacroRegime.RISK_ON,
        confirmation_type="V",
        tranche_stage=1,
        suggested_tranche_usd=Decimal("900"),
        suggested_whole_shares=Decimal("18"),
        reasons=("confirmed_v",),
        expires_at=NOW + timedelta(days=56),
    )

    assert transition.transition_id.version == 7
    assert transition.tranche_stage == 1
    with pytest.raises(ValidationError, match="must change"):
        PatreonCapsTransition(
            **{**transition.model_dump(), "state": PatreonCapsState.SUPPORT_TEST}
        )
