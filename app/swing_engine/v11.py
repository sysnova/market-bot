"""Optional higher-timeframe Support Confirmation enrichment for Swing v11."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from app.contracts import (
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
    SupportAssessment,
    SupportState,
)

from .models import SwingContext
from .v10 import SwingEngineV10

_IGNORED_STATES = {
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
    SupportState.SINGLE_SUPPORT_NEARBY,
    SupportState.B_WAVE_RISK,
    SupportState.INVALIDATED,
    SupportState.EXPIRED,
}
_STRUCTURE_STATES = {
    SupportState.STRUCTURE_CONFIRMED,
    SupportState.RETEST_CONFIRMED,
}
_REACTION_STATES = {
    SupportState.REACTION_CONFIRMED,
    SupportState.LIQUIDITY_SWEEP,
    SupportState.RECLAIMED,
}


class SwingEngineV11(SwingEngineV10):
    """Attach fresh, independent Support evidence without gating native Swing."""

    engine_version = "11.0.0"

    def __init__(self, *, support_freshness_sessions: int = 3, **kwargs: object) -> None:
        if support_freshness_sessions < 1:
            raise ValueError("support freshness sessions must be positive")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._support_freshness_sessions = support_freshness_sessions

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        contribution = _support_contribution(
            context,
            result,
            freshness_sessions=self._support_freshness_sessions,
            classifier=self._classify_support,
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
                    *self._support_metrics(support, strength, matched_zone),
                ),
                "source_event_ids": tuple(
                    dict.fromkeys((*result.source_event_ids, support.assessment_id))
                ),
                "context_hash": _enriched_hash(result.context_hash, support),
            }
        )

    def _classify_support(self, support: SupportAssessment, current_price: Decimal) -> str | None:
        del current_price
        if support.b_wave_risk:
            return None
        if support.state in _STRUCTURE_STATES and support.reversal_score >= Decimal("60"):
            return "STRUCTURE"
        if support.state in _REACTION_STATES and support.reaction_score >= Decimal("60"):
            return "REACTION"
        if any(_higher_timeframe_source(source) for source in support.support_sources):
            return "ZONE"
        return None

    def _support_metrics(
        self,
        support: SupportAssessment,
        strength: str,
        matched_zone: str,
    ) -> tuple[NamedValue, ...]:
        return (
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
        )


def _support_contribution(
    context: SwingContext,
    result: AnalysisResult,
    *,
    freshness_sessions: int,
    classifier: Callable[[SupportAssessment, Decimal], str | None],
) -> tuple[SupportAssessment, str, str] | None:
    support = context.support
    if (
        support is None
        or result.direction is not PatternDirection.BULLISH
        or result.verdict is AnalysisVerdict.AVOID
        or support.state in _IGNORED_STATES
        or support.zone_low is None
        or support.zone_high is None
        or support.invalidation is None
        or context.price <= support.invalidation
    ):
        return None
    cutoff_index = max(0, len(context.daily_bars) - freshness_sessions)
    support_as_of = support.data_as_of or support.occurred_at
    if not context.daily_bars or support_as_of < context.daily_bars[cutoff_index].timestamp:
        return None

    values = {item.name: item.value for item in result.metrics}
    entry_low = _decimal(values.get("entry_zone_low"))
    entry_high = _decimal(values.get("entry_zone_high"))
    reaction_low = _decimal(values.get("recovery_reaction_low"))
    native_support = _decimal(values.get("support"))
    matched_zone: str | None = None
    if (
        entry_low is not None
        and entry_high is not None
        and _overlaps(entry_low, entry_high, support.zone_low, support.zone_high)
    ):
        matched_zone = "ENTRY_ZONE"
    elif reaction_low is not None and support.zone_low <= reaction_low <= support.zone_high:
        matched_zone = "RECOVERY_LOW"
    elif native_support is not None and support.zone_low <= native_support <= support.zone_high:
        matched_zone = "STRUCTURAL_SUPPORT"
    if matched_zone is None:
        return None

    strength = classifier(support, context.price)
    if strength is None:
        return None
    return support, strength, matched_zone


def _higher_timeframe_source(source: str) -> bool:
    return source.startswith("weekly_") or source.startswith("pivot_weekly_")


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, support: SupportAssessment) -> str:
    payload = f"{context_hash}|support:{support.assessment_id}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
