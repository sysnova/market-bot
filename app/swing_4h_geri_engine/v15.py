"""Optional Support Confirmation enrichment for standalone 4HGERI v1.5."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal

from app.contracts import (
    GeriAssessment,
    GeriMaturity,
    NamedValue,
    SupportAssessment,
    SupportState,
    TradeSide,
)

from .engine import Swing4HGeriEngineV14
from .models import Swing4HGeriContext

_IGNORED_SUPPORT_STATES = {
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
    SupportState.B_WAVE_RISK,
    SupportState.INVALIDATED,
    SupportState.EXPIRED,
}
_NON_ACTIONABLE_GERI_STATES = {
    GeriMaturity.BUILDING,
    GeriMaturity.EXTENDED,
    GeriMaturity.RECLAIM_REQUIRED,
    GeriMaturity.INVALIDATED,
}
_STRUCTURE_SUPPORT_STATES = {
    SupportState.STRUCTURE_CONFIRMED,
    SupportState.RETEST_CONFIRMED,
}
_REACTION_SUPPORT_STATES = {
    SupportState.REACTION_CONFIRMED,
    SupportState.LIQUIDITY_SWEEP,
    SupportState.RECLAIMED,
}


class Swing4HGeriEngineV15(Swing4HGeriEngineV14):
    """Annotate matching main or tactical LONG zones with daily/weekly support."""

    engine_version = "1.5.0"

    def __init__(self, *, support_freshness_days: int = 8, **kwargs: object) -> None:
        if support_freshness_days < 1:
            raise ValueError("support freshness days must be positive")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._support_freshness_days = support_freshness_days

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        result = super().analyze(context)
        contribution = _support_contribution(
            context,
            result,
            freshness_days=self._support_freshness_days,
        )
        if contribution is None:
            return result.model_copy(update={"engine_version": self.engine_version})
        support, strength, matched_zone = contribution
        reason = f"support_confirmation_{strength.lower()}_confluence"
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": tuple(dict.fromkeys((*result.reasons, reason))),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="support_assessment_id", value=str(support.assessment_id)),
                    NamedValue(name="support_contribution", value=strength),
                    NamedValue(name="support_state", value=support.state.value),
                    NamedValue(
                        name="support_confirmation_type",
                        value=support.confirmation_type.value,
                    ),
                    NamedValue(name="support_zone_low", value=support.zone_low),
                    NamedValue(name="support_zone_high", value=support.zone_high),
                    NamedValue(name="support_zone_match", value=matched_zone),
                    NamedValue(name="support_reaction_score", value=support.reaction_score),
                    NamedValue(name="support_reversal_score", value=support.reversal_score),
                    NamedValue(name="support_sources", value=support.support_sources),
                ),
                "context_hash": _enriched_hash(result.context_hash, support),
            }
        )


def _support_contribution(
    context: Swing4HGeriContext,
    result: GeriAssessment,
    *,
    freshness_days: int,
) -> tuple[SupportAssessment, str, str] | None:
    support = context.support
    reference_at = context.current_price_at or context.as_of
    if (
        support is None
        or reference_at is None
        or support.symbol != result.symbol
        or support.state in _IGNORED_SUPPORT_STATES
        or support.b_wave_risk
        or support.zone_low is None
        or support.zone_high is None
        or support.invalidation is None
        or result.current_price <= support.invalidation
    ):
        return None
    support_as_of = support.data_as_of or support.occurred_at
    if support_as_of > reference_at or support_as_of < reference_at - timedelta(
        days=freshness_days
    ):
        return None

    matched_zone: str | None = None
    if (
        result.trade_side is TradeSide.LONG
        and result.maturity not in _NON_ACTIONABLE_GERI_STATES
        and result.zone_low is not None
        and result.zone_high is not None
        and _overlaps(result.zone_low, result.zone_high, support.zone_low, support.zone_high)
    ):
        matched_zone = "MAIN"
    else:
        values = {item.name: item.value for item in result.metrics}
        tactical_side_value = values.get("countertrend_side")
        tactical_state_value = values.get("countertrend_state")
        tactical_side = getattr(tactical_side_value, "value", tactical_side_value)
        tactical_state = getattr(tactical_state_value, "value", tactical_state_value)
        tactical_low = _decimal(values.get("countertrend_zone_low"))
        tactical_high = _decimal(values.get("countertrend_zone_high"))
        if (
            tactical_side == TradeSide.LONG.value
            and tactical_state not in {item.value for item in _NON_ACTIONABLE_GERI_STATES}
            and values.get("countertrend_eligible") is True
            and values.get("countertrend_expired") is not True
            and tactical_low is not None
            and tactical_high is not None
            and _overlaps(tactical_low, tactical_high, support.zone_low, support.zone_high)
        ):
            matched_zone = "TACTICAL"
    if matched_zone is None:
        return None

    if support.state in _STRUCTURE_SUPPORT_STATES and support.reversal_score >= Decimal("60"):
        strength = "STRUCTURE"
    elif support.state in _REACTION_SUPPORT_STATES and support.reaction_score >= Decimal("60"):
        strength = "REACTION"
    else:
        strength = "ZONE"
    return support, strength, matched_zone


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _upsert_metrics(result: GeriAssessment, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, support: SupportAssessment) -> str:
    payload = f"{context_hash}|support:{support.assessment_id}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
