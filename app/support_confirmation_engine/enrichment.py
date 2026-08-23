"""Shared policy for consuming Support without replacing native engine geometry."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from app.contracts import SupportAssessment, SupportState, SupportZonePosition

SupportContribution = Literal["CONTEXT", "REACTION", "STRUCTURE"]

_IGNORED_STATES = {
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
    SupportState.SINGLE_SUPPORT_NEARBY,
    SupportState.B_WAVE_RISK,
    SupportState.INVALIDATED,
    SupportState.EXPIRED,
}
_CONTEXT_STATES = {
    SupportState.WATCH_KEY_SUPPORT,
    SupportState.FIRST_TOUCH,
    SupportState.BASE_BUILDING,
    SupportState.LIQUIDITY_SWEEP,
}
_REACTION_STATES = {
    SupportState.REACTION_CONFIRMED,
    SupportState.RECLAIMED,
}
_STRUCTURE_STATES = {
    SupportState.STRUCTURE_CONFIRMED,
    SupportState.RETEST_CONFIRMED,
}


def classify_support_enrichment(
    support: SupportAssessment,
    *,
    current_price: Decimal,
) -> SupportContribution | None:
    """Classify corroborating evidence after a consumer matches its own native zone."""

    if (
        support.state in _IGNORED_STATES
        or support.zone_low is None
        or support.zone_high is None
        or support.invalidation is None
        or current_price <= support.invalidation
        or support.zone_position is SupportZonePosition.BELOW_ZONE
        or current_price < support.zone_low
    ):
        return None

    is_v03 = str(support.engine_version).startswith("0.3.")
    distance_atr = support.zone_distance_atr
    if (
        not support.b_wave_risk
        and support.state in _STRUCTURE_STATES
        and support.reversal_score >= Decimal("60")
    ):
        if not _within_distance(distance_atr, Decimal("1.5")):
            return None
        if not _fresh_structure(support, is_v03=is_v03):
            return None
        if is_v03 and support.actionability_score < Decimal("55"):
            return None
        return "STRUCTURE"

    if (
        not support.b_wave_risk
        and support.state in _REACTION_STATES
        and support.reaction_score >= Decimal("60")
    ):
        if not _within_distance(distance_atr, Decimal("1.0")):
            return None
        if not _fresh_reaction(support, is_v03=is_v03):
            return None
        if is_v03 and support.actionability_score < Decimal("45"):
            return None
        return "REACTION"

    context_state = support.state in _CONTEXT_STATES or (
        support.b_wave_risk and support.state in _REACTION_STATES
    )
    if not context_state or len(support.support_sources) < 2:
        return None
    if not _within_distance(distance_atr, Decimal("0.75")):
        return None
    if is_v03 and support.actionability_score < Decimal("20"):
        return None
    if support.touch_age_sessions is not None and support.touch_age_sessions > 3:
        return None
    return "CONTEXT"


def _within_distance(value: Decimal | None, maximum: Decimal) -> bool:
    return value is None or value <= maximum


def _fresh_reaction(support: SupportAssessment, *, is_v03: bool) -> bool:
    if support.four_hour_reclaim:
        return True
    if support.touch_age_sessions is None:
        return not is_v03
    return support.touch_age_sessions <= 5


def _fresh_structure(support: SupportAssessment, *, is_v03: bool) -> bool:
    if support.four_hour_higher_high and support.four_hour_higher_low:
        return True
    if support.touch_age_sessions is None:
        return not is_v03
    return support.touch_age_sessions <= 10
