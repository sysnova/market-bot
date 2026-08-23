"""GERI tactical-zone and Support Confirmation enrichment for SwingTrade v1.2."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from app.contracts import (
    GeriLevelKind,
    GeriMaturity,
    NamedValue,
    SupportAssessment,
    SupportState,
    SwingTradeAssessment,
    SwingTradeMaturity,
    TradeSide,
)

from .engine import SwingTradeEngineV11
from .models import SwingTradeContext

_NON_ACTIONABLE_GERI_STATES = {
    GeriMaturity.BUILDING,
    GeriMaturity.EXTENDED,
    GeriMaturity.RECLAIM_REQUIRED,
    GeriMaturity.INVALIDATED,
}
_GERI_REACTION_STATES = {GeriMaturity.L2_4H, GeriMaturity.L3, GeriMaturity.L4}
_IGNORED_SUPPORT_STATES = {
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
    SupportState.B_WAVE_RISK,
    SupportState.INVALIDATED,
    SupportState.EXPIRED,
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


class SwingTradeEngineV12(SwingTradeEngineV11):
    """Use GERI's tactical LONG zone and annotate independent Support confluence."""

    engine_version = "1.2.0"

    def __init__(self, *, support_freshness_sessions: int = 3, **kwargs: object) -> None:
        if support_freshness_sessions < 1:
            raise ValueError("support freshness sessions must be positive")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._support_freshness_sessions = support_freshness_sessions

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        _validate_support_time(context)
        result = super().analyze(context)
        geri = _selected_geri_confluence(
            context,
            result,
            freshness_sessions=self._geri_freshness,
        )
        maturity = result.maturity
        reasons = [reason for reason in result.reasons if not reason.startswith("swing_trade_st")]
        reasons = [reason for reason in reasons if reason != "geri_reaction_confirmation_pending"]
        updates: dict[str, object] = {"engine_version": self.engine_version}
        metrics: list[NamedValue] = []

        if geri is not None:
            source, zone_low, zone_high, reaction = geri
            updates.update(
                geri_assessment_id=context.geri.assessment_id if context.geri is not None else None,
                geri_zone_low=zone_low,
                geri_zone_high=zone_high,
                geri_confluence=True,
            )
            reasons.append(f"geri_{source.lower()}_confluence")
            if reaction:
                reasons.append(f"geri_{source.lower()}_reaction_confirmed")
                if (
                    maturity is SwingTradeMaturity.ST3
                    and _metric(result, "swing_trade_entry_trigger_passed") is True
                ):
                    maturity = SwingTradeMaturity.ST4
            else:
                reasons.append("geri_reaction_confirmation_pending")
            metrics.extend(
                (
                    NamedValue(name="geri_zone_source", value=source),
                    NamedValue(name="geri_reaction_confirmed", value=reaction),
                )
            )

        support = _support_contribution(
            context,
            result,
            freshness_sessions=self._support_freshness_sessions,
        )
        if support is not None:
            support_item, strength = support
            reasons.append(f"support_confirmation_{strength.lower()}_confluence")
            metrics.extend(
                (
                    NamedValue(name="support_assessment_id", value=str(support_item.assessment_id)),
                    NamedValue(name="support_contribution", value=strength),
                    NamedValue(name="support_state", value=support_item.state.value),
                    NamedValue(
                        name="support_confirmation_type",
                        value=support_item.confirmation_type.value,
                    ),
                    NamedValue(name="support_zone_low", value=support_item.zone_low),
                    NamedValue(name="support_zone_high", value=support_item.zone_high),
                    NamedValue(name="support_reaction_score", value=support_item.reaction_score),
                    NamedValue(name="support_reversal_score", value=support_item.reversal_score),
                    NamedValue(name="support_sources", value=support_item.support_sources),
                )
            )
            updates["context_hash"] = _enriched_hash(result.context_hash, support_item)

        if maturity is not None:
            reasons.insert(0, f"swing_trade_{maturity.value.lower()}")
        else:
            reasons.insert(0, "swing_trade_no_thesis")
        updates.update(
            maturity=maturity,
            eligible=maturity is not None,
            reasons=tuple(dict.fromkeys(reasons)),
            metrics=_upsert_metrics(result, *metrics),
        )
        return result.model_copy(update=updates)


def _selected_geri_confluence(
    context: SwingTradeContext,
    result: SwingTradeAssessment,
    *,
    freshness_sessions: int,
) -> tuple[str, Decimal, Decimal, bool] | None:
    geri = context.geri
    if geri is None or not geri.standalone_swing:
        return None
    cutoff_index = max(0, len(context.daily_bars) - freshness_sessions)
    if not context.daily_bars or geri.occurred_at < context.daily_bars[cutoff_index].timestamp:
        return None

    if (
        geri.trade_side is TradeSide.LONG
        and geri.active_level_kind is GeriLevelKind.SUPPORT
        and geri.maturity not in _NON_ACTIONABLE_GERI_STATES
        and geri.zone_low is not None
        and geri.zone_high is not None
        and geri.zone_low <= context.current_price <= geri.zone_high
        and _overlaps(result.zone_low, result.zone_high, geri.zone_low, geri.zone_high)
    ):
        return "MAIN", geri.zone_low, geri.zone_high, geri.maturity in _GERI_REACTION_STATES

    values = {item.name: item.value for item in geri.metrics}
    side = getattr(values.get("countertrend_side"), "value", values.get("countertrend_side"))
    state_value = getattr(
        values.get("countertrend_state"), "value", values.get("countertrend_state")
    )
    zone_low = _decimal(values.get("countertrend_zone_low"))
    zone_high = _decimal(values.get("countertrend_zone_high"))
    if (
        side != TradeSide.LONG.value
        or state_value in {state.value for state in _NON_ACTIONABLE_GERI_STATES}
        or values.get("countertrend_eligible") is not True
        or values.get("countertrend_expired") is True
        or zone_low is None
        or zone_high is None
        or not zone_low <= context.current_price <= zone_high
        or not _overlaps(result.zone_low, result.zone_high, zone_low, zone_high)
    ):
        return None
    reaction = state_value in {state.value for state in _GERI_REACTION_STATES}
    return "COUNTERTREND", zone_low, zone_high, reaction


def _support_contribution(
    context: SwingTradeContext,
    result: SwingTradeAssessment,
    *,
    freshness_sessions: int,
) -> tuple[SupportAssessment, str] | None:
    support = context.support
    if (
        support is None
        or result.maturity is None
        or support.state in _IGNORED_SUPPORT_STATES
        or support.b_wave_risk
        or support.zone_low is None
        or support.zone_high is None
        or support.invalidation is None
        or context.current_price <= support.invalidation
        or not _overlaps(result.zone_low, result.zone_high, support.zone_low, support.zone_high)
    ):
        return None
    cutoff_index = max(0, len(context.daily_bars) - freshness_sessions)
    support_as_of = support.data_as_of or support.occurred_at
    if not context.daily_bars or support_as_of < context.daily_bars[cutoff_index].timestamp:
        return None

    if support.state in _STRUCTURE_SUPPORT_STATES and support.reversal_score >= Decimal("60"):
        strength = "STRUCTURE"
    elif support.state in _REACTION_SUPPORT_STATES and support.reaction_score >= Decimal("60"):
        strength = "REACTION"
    elif any(_independent_support_source(source) for source in support.support_sources):
        strength = "ZONE"
    else:
        return None
    return support, strength


def _independent_support_source(source: str) -> bool:
    return (
        source.startswith("weekly_")
        or source.startswith("pivot_weekly_")
        or source.startswith("daily_sma")
    )


def _validate_support_time(context: SwingTradeContext) -> None:
    support = context.support
    if support is None:
        return
    symbol = context.symbol.strip().upper()
    support_as_of = support.data_as_of or support.occurred_at
    if support.symbol != symbol:
        raise ValueError("SwingTrade Support evidence must belong to the requested symbol")
    if support_as_of > context.as_of:
        raise ValueError("SwingTrade Support evidence is later than as_of")
    if context.current_price_at is not None and support_as_of > context.current_price_at:
        raise ValueError("SwingTrade Support evidence is later than current_price_at")


def _metric(result: SwingTradeAssessment, name: str) -> object | None:
    return next((item.value for item in result.metrics if item.name == name), None)


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _upsert_metrics(result: SwingTradeAssessment, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, support: SupportAssessment) -> str:
    payload = f"{context_hash}|support:{support.assessment_id}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
