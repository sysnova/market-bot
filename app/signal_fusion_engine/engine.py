"""Pure decision policy that fuses independent evidence without double-counting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    FusionAssessment,
    FusionState,
    PatreonCapsAssessment,
    PatreonCapsState,
    PatternDirection,
    SupportAssessment,
    SupportState,
    WaveAssessment,
    WavePhase,
)

from .models import SignalFusionContext

ZERO = Decimal()
HUNDRED = Decimal("100")
FOUR_PLACES = Decimal("0.0001")
MIN_REWARD_RISK = Decimal("2")


class SignalFusionEngine:
    """Require independent structure, trend, timing, execution, and risk gates."""

    engine_id = "signal-fusion"
    engine_version = "0.3.0"

    def evaluate(self, context: SignalFusionContext) -> FusionAssessment:
        analyses = {item.horizon: item for item in context.analyses}
        support = context.support
        wave = context.wave
        long_term = analyses.get(AnalysisHorizon.LONG_TERM)
        swing = analyses.get(AnalysisHorizon.SWING)
        intraday = analyses.get(AnalysisHorizon.INTRADAY)
        dilution = analyses.get(AnalysisHorizon.DILUTION)
        current_price = _current_price(intraday, support, wave)

        missing = tuple(
            name
            for name, value in (
                ("SUPPORT", support),
                ("ELLIOTT", wave),
                (AnalysisHorizon.LONG_TERM.value, long_term),
                (AnalysisHorizon.SWING.value, swing),
                (AnalysisHorizon.INTRADAY.value, intraday),
            )
            if value is None
        )
        support_zone_gate = _support_zone_gate(support)
        support_reaction_gate = bool(
            support_zone_gate
            and support is not None
            and support.reaction_score >= Decimal("60")
        )
        support_gate = bool(
            support is not None
            and support.state
            in {SupportState.STRUCTURE_CONFIRMED, SupportState.RETEST_CONFIRMED}
            and support.reversal_score >= Decimal("60")
            and not support.b_wave_risk
        )
        trend_gate = _bullish(long_term, minimum_score=Decimal("65"))
        swing_gate = _bullish(swing, minimum_score=Decimal("60"))
        wave_gate = _wave_gate(wave, current_price)
        timing_gate = swing_gate or wave_gate
        execution_gate = _execution_gate(intraday)
        dilution_gate = dilution is None or dilution.verdict not in {
            AnalysisVerdict.CAUTION,
            AnalysisVerdict.AVOID,
        }
        portfolio_gate = context.holding_quantity > ZERO

        trigger = _trigger_price(wave, intraday, current_price)
        standard_invalidation = _invalidation(context, current_price)
        standard_target = _target(context, current_price)
        standard_reward_risk = _reward_risk(
            current_price, standard_invalidation, standard_target
        )
        standard_reward_risk_gate = bool(
            standard_reward_risk is not None
            and standard_reward_risk >= MIN_REWARD_RISK
        )
        recovery_invalidation = _recovery_invalidation(intraday, current_price)
        recovery_target = _recovery_target(wave, swing, current_price)
        recovery_reward_risk = _reward_risk(
            current_price, recovery_invalidation, recovery_target
        )
        recovery_reward_risk_gate = bool(
            recovery_reward_risk is not None
            and recovery_reward_risk >= MIN_REWARD_RISK
        )

        invalidated = bool(
            support is not None
            and support.state in {SupportState.INVALIDATED, SupportState.EXPIRED}
        ) or bool(
            context.patreon is not None
            and context.patreon.state
            in {PatreonCapsState.INVALIDATED, PatreonCapsState.EXPIRED}
        )
        all_gates = all(
            (
                support_gate,
                trend_gate,
                timing_gate,
                execution_gate,
                dilution_gate,
                portfolio_gate,
                standard_reward_risk_gate,
            )
        )
        recovery_gate = all(
            (
                support_zone_gate,
                support_reaction_gate,
                wave_gate,
                execution_gate,
                dilution_gate,
                portfolio_gate,
                recovery_reward_risk_gate,
            )
        )
        if invalidated:
            state = FusionState.INVALIDATED
        elif not dilution_gate or not portfolio_gate:
            state = FusionState.VETOED
        elif missing:
            state = FusionState.INCOMPLETE
        elif all_gates:
            state = FusionState.BUY_CONFIRMED
        elif recovery_gate:
            state = FusionState.RECOVERY_CONFIRMED
        elif support_gate and trend_gate and timing_gate:
            state = FusionState.ARMED
        else:
            state = FusionState.OBSERVING

        if state is FusionState.RECOVERY_CONFIRMED:
            invalidation = recovery_invalidation
            target = recovery_target
            reward_risk = recovery_reward_risk
            reward_risk_gate = recovery_reward_risk_gate
        else:
            invalidation = standard_invalidation
            target = standard_target
            reward_risk = standard_reward_risk
            reward_risk_gate = standard_reward_risk_gate

        score = _score(
            support,
            support_zone_gate=support_zone_gate,
            support_reaction_gate=support_reaction_gate,
            support_gate=support_gate,
            trend_gate=trend_gate,
            swing_gate=swing_gate,
            wave_gate=wave_gate,
            execution_gate=execution_gate,
            dilution_gate=dilution_gate,
            dilution_available=dilution is not None,
            portfolio_gate=portfolio_gate,
            reward_risk_gate=reward_risk_gate,
        )
        reasons = _reasons(
            state=state,
            missing=missing,
            support_gate=support_gate,
            trend_gate=trend_gate,
            swing_gate=swing_gate,
            wave_gate=wave_gate,
            execution_gate=execution_gate,
            dilution_gate=dilution_gate,
            dilution_available=dilution is not None,
            portfolio_gate=portfolio_gate,
            reward_risk_gate=reward_risk_gate,
            recovery_gate=recovery_gate,
            support=support,
            patreon=context.patreon,
        )
        assessment_ids = tuple(
            item.assessment_id
            for item in (support, wave, context.patreon)
            if item is not None
        )
        occurred_at = max(
            item
            for item in (
                support.occurred_at if support is not None else None,
                wave.occurred_at if wave is not None else None,
                context.patreon.occurred_at if context.patreon is not None else None,
                *(analysis.as_of for analysis in context.analyses),
            )
            if item is not None
        )
        return FusionAssessment(
            symbol=context.symbol.strip().upper(),
            occurred_at=occurred_at,
            engine_version=self.engine_version,
            state=state,
            score=_rounded(score),
            confidence=_rounded(score / HUNDRED),
            current_price=current_price,
            support_zone_gate=support_zone_gate,
            support_reaction_gate=support_reaction_gate,
            support_gate=support_gate,
            trend_gate=trend_gate,
            timing_gate=timing_gate,
            execution_gate=execution_gate,
            dilution_gate=dilution_gate,
            portfolio_gate=portfolio_gate,
            reward_risk_gate=reward_risk_gate,
            recovery_gate=recovery_gate,
            trigger_price=trigger,
            entry_price=current_price if invalidation is not None and target is not None else None,
            invalidation=invalidation,
            target_price=target,
            reward_risk_ratio=_rounded(reward_risk) if reward_risk is not None else None,
            patreon_context=(
                context.patreon.state.value if context.patreon is not None else None
            ),
            dilution_context=(
                dilution.verdict.value if dilution is not None else "UNAVAILABLE"
            ),
            missing_sources=missing,
            source_assessment_ids=assessment_ids,
            source_analysis_ids=tuple(item.analysis_id for item in context.analyses),
            reasons=reasons,
            context_hash=_context_hash(context),
        )


def _require_wave(wave: WaveAssessment | None) -> WaveAssessment:
    if wave is None:
        raise ValueError("Signal Fusion has no price source")
    return wave


def _current_price(
    intraday: AnalysisResult | None,
    support: SupportAssessment | None,
    wave: WaveAssessment | None,
) -> Decimal:
    candidates: list[tuple[datetime, Decimal]] = []
    intraday_price = _decimal_metric(intraday, "reference_price")
    if intraday is not None and intraday_price is not None:
        candidates.append((intraday.as_of, intraday_price))
    if support is not None:
        candidates.append((support.occurred_at, support.current_price))
    if wave is not None:
        candidates.append((wave.occurred_at, wave.current_price))
    if not candidates:
        return _require_wave(wave).current_price
    return max(candidates, key=lambda item: item[0])[1]


def _support_zone_gate(support: SupportAssessment | None) -> bool:
    if support is None or support.state in {
        SupportState.NO_KEY_SUPPORT,
        SupportState.NO_NEARBY_SUPPORT,
        SupportState.INVALIDATED,
        SupportState.EXPIRED,
    }:
        return False
    return bool(
        support.zone_low is not None
        and support.zone_center is not None
        and support.zone_high is not None
        and support.invalidation is not None
        and support.current_price >= support.invalidation
    )


def _bullish(result: AnalysisResult | None, *, minimum_score: Decimal) -> bool:
    return bool(
        result is not None
        and result.verdict is AnalysisVerdict.FAVORABLE
        and result.direction is PatternDirection.BULLISH
        and result.score >= minimum_score
        and result.confidence >= Decimal("0.60")
    )


def _wave_gate(wave: WaveAssessment | None, current_price: Decimal) -> bool:
    if wave is None:
        return False
    if wave.phase in {WavePhase.WAVE_3_ACTIVE, WavePhase.WAVE_5_ACTIVE}:
        return True
    return bool(
        wave.phase in {WavePhase.WAVE_2_ENDING, WavePhase.WAVE_4_ENDING}
        and wave.trigger_price is not None
        and current_price >= wave.trigger_price
    )


def _execution_gate(result: AnalysisResult | None) -> bool:
    return bool(
        _bullish(result, minimum_score=Decimal("60"))
        and _metric(result, "confirmation_gate_passed") is True
    )


def _metric(result: AnalysisResult | None, name: str) -> object | None:
    if result is None:
        return None
    return next((item.value for item in result.metrics if item.name == name), None)


def _decimal_metric(result: AnalysisResult | None, *names: str) -> Decimal | None:
    for name in names:
        value = _metric(result, name)
        if isinstance(value, (Decimal, int, str)) and not isinstance(value, bool):
            try:
                return Decimal(str(value))
            except ArithmeticError:
                continue
    return None


def _trigger_price(
    wave: WaveAssessment | None,
    intraday: AnalysisResult | None,
    current: Decimal,
) -> Decimal:
    if wave is not None and wave.trigger_price is not None:
        return wave.trigger_price
    return _decimal_metric(intraday, "reference_price") or current


def _invalidation(context: SignalFusionContext, current: Decimal) -> Decimal | None:
    analyses = {item.horizon: item for item in context.analyses}
    values = (
        context.support.invalidation if context.support is not None else None,
        context.wave.invalidation if context.wave is not None else None,
        _decimal_metric(analyses.get(AnalysisHorizon.SWING), "invalidation"),
        _decimal_metric(
            analyses.get(AnalysisHorizon.INTRADAY), "invalidation_level"
        ),
    )
    valid = tuple(item for item in values if item is not None and ZERO < item < current)
    return max(valid, default=None)


def _target(context: SignalFusionContext, current: Decimal) -> Decimal | None:
    analyses = {item.horizon: item for item in context.analyses}
    values = (
        context.wave.target_low if context.wave is not None else None,
        _decimal_metric(analyses.get(AnalysisHorizon.SWING), "target_2r"),
        _decimal_metric(analyses.get(AnalysisHorizon.INTRADAY), "objective_level"),
    )
    valid = tuple(item for item in values if item is not None and item > current)
    return min(valid, default=None)


def _recovery_invalidation(
    intraday: AnalysisResult | None, current: Decimal
) -> Decimal | None:
    invalidation = _decimal_metric(intraday, "invalidation_level")
    if invalidation is None or not ZERO < invalidation < current:
        return None
    return invalidation


def _recovery_target(
    wave: WaveAssessment | None,
    swing: AnalysisResult | None,
    current: Decimal,
) -> Decimal | None:
    values = (
        wave.target_low if wave is not None else None,
        _decimal_metric(swing, "target_2r"),
    )
    valid = tuple(item for item in values if item is not None and item > current)
    return min(valid, default=None)


def _reward_risk(
    entry: Decimal, invalidation: Decimal | None, target: Decimal | None
) -> Decimal | None:
    if invalidation is None or target is None or invalidation >= entry or target <= entry:
        return None
    return (target - entry) / (entry - invalidation)


def _score(
    support: SupportAssessment | None,
    *,
    support_zone_gate: bool,
    support_reaction_gate: bool,
    support_gate: bool,
    trend_gate: bool,
    swing_gate: bool,
    wave_gate: bool,
    execution_gate: bool,
    dilution_gate: bool,
    dilution_available: bool,
    portfolio_gate: bool,
    reward_risk_gate: bool,
) -> Decimal:
    support_points = Decimal("30") if support_gate else ZERO
    if not support_gate and support is not None:
        support_points = support.reversal_score * Decimal("0.30")
        if support_zone_gate:
            support_points = max(support_points, Decimal("10"))
        if support_reaction_gate:
            support_points = max(support_points, Decimal("15"))
    return min(
        HUNDRED,
        support_points
        + (Decimal("20") if trend_gate else ZERO)
        + (Decimal("10") if swing_gate else ZERO)
        + (Decimal("10") if wave_gate else ZERO)
        + (Decimal("15") if execution_gate else ZERO)
        + (Decimal("5") if dilution_gate and dilution_available else ZERO)
        + (Decimal("5") if portfolio_gate else ZERO)
        + (Decimal("5") if reward_risk_gate else ZERO),
    )


def _reasons(
    *,
    state: FusionState,
    missing: tuple[str, ...],
    support_gate: bool,
    trend_gate: bool,
    swing_gate: bool,
    wave_gate: bool,
    execution_gate: bool,
    dilution_gate: bool,
    dilution_available: bool,
    portfolio_gate: bool,
    reward_risk_gate: bool,
    recovery_gate: bool,
    support: SupportAssessment | None,
    patreon: PatreonCapsAssessment | None,
) -> tuple[str, ...]:
    reasons: list[str] = [f"fusion_state_{state.value.lower()}"]
    if missing:
        reasons.append(f"missing_sources:{','.join(missing)}")
    if not support_gate:
        reasons.append("support_structure_unconfirmed")
    if not trend_gate:
        reasons.append("long_trend_unconfirmed")
    if not swing_gate and not wave_gate:
        reasons.append("swing_and_elliott_timing_unconfirmed")
    if not execution_gate:
        reasons.append("intraday_execution_unconfirmed")
    if not dilution_gate:
        reasons.append("sec_dilution_veto")
    elif not dilution_available:
        reasons.append("dilution_assessment_unavailable")
    if not portfolio_gate:
        reasons.append("portfolio_holding_required")
    if not reward_risk_gate:
        reasons.append("reward_risk_below_two")
    if recovery_gate:
        reasons.extend(
            (
                "elliott_trigger_with_intraday_confirmation",
                "recovery_entry_tactical_size_only",
            )
        )
        if not support_gate:
            reasons.append("support_structure_pending_for_scale_in")
        if not trend_gate:
            reasons.append("long_trend_pending_for_scale_in")
        if support is not None and support.b_wave_risk:
            reasons.append("support_b_wave_risk")
    if patreon is not None:
        reasons.append(f"patreon_context:{patreon.state.value}")
    return tuple(reasons)


def _context_hash(context: SignalFusionContext) -> str:
    payload = {
        "symbol": context.symbol.strip().upper(),
        "holding_quantity": str(context.holding_quantity),
        "support": str(context.support.assessment_id) if context.support else None,
        "wave": str(context.wave.assessment_id) if context.wave else None,
        "patreon": str(context.patreon.assessment_id) if context.patreon else None,
        "analyses": sorted(str(item.analysis_id) for item in context.analyses),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
