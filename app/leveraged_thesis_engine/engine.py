"""Deterministic thesis selection for long and inverse daily instruments."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.common.canonical import sha256_digest
from app.contracts import (
    MarketSession,
    OrderFlowState,
    OrderFlowStateKind,
    PatternDirection,
    SupportAssessment,
    SupportState,
    SupportZonePosition,
)
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisState,
    LeveragedThesisTransition,
)

from .models import (
    LeveragedPair,
    LeveragedThesisContext,
    LeveragedThesisEvaluation,
)

_BUY_FLOW = {
    OrderFlowStateKind.BUY_PRESSURE,
    OrderFlowStateKind.SELLER_EXHAUSTION,
    OrderFlowStateKind.BUY_ABSORPTION,
    OrderFlowStateKind.BULLISH_DIVERGENCE,
}
_SELL_FLOW = {
    OrderFlowStateKind.SELL_PRESSURE,
    OrderFlowStateKind.BUYER_EXHAUSTION,
    OrderFlowStateKind.SELL_ABSORPTION,
    OrderFlowStateKind.BEARISH_DIVERGENCE,
}
_BULLISH_SETUPS = {"bullish_breakout", "bullish_vwap_reclaim"}
_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}
_SUPPORT_CONTEXT_STATES = {
    SupportState.WATCH_KEY_SUPPORT,
    SupportState.FIRST_TOUCH,
    SupportState.BASE_BUILDING,
    SupportState.LIQUIDITY_SWEEP,
}
_SUPPORT_REACTION_STATES = {
    SupportState.REACTION_CONFIRMED,
    SupportState.RECLAIMED,
}
_SUPPORT_STRUCTURE_STATES = {
    SupportState.STRUCTURE_CONFIRMED,
    SupportState.RETEST_CONFIRMED,
}
_NO_NEARBY_SUPPORT_STATES = {
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
}


class LeveragedThesisEngine:
    """Fuse underlying structure and cross-instrument SIP L1 evidence."""

    engine_id = "leveraged-thesis"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        pairs: tuple[LeveragedPair, ...] | None = None,
        minimum_structure_score: Decimal = Decimal("68"),
        minimum_flow_confidence: Decimal = Decimal("0.65"),
        minimum_data_quality: Decimal = Decimal("0.70"),
        maximum_underlying_flow_age_ms: int = 3_000,
        maximum_instrument_flow_age_ms: int = 4_000,
        maximum_quote_age_ms: int = 2_000,
        maximum_spread_bps: Decimal = Decimal("35"),
        maximum_support_age_ms: int = 28_800_000,
        maximum_support_distance_percent: Decimal = Decimal("3"),
    ) -> None:
        self.pairs = pairs or (
            LeveragedPair("ASTS", "ASTX", "ASTN"),
            LeveragedPair(
                "NBIS",
                "NBIS",
                "NBIZ",
                bullish_exposure=LeveragedExposure.LONG_1X,
            ),
        )
        self._minimum_structure_score = minimum_structure_score
        self._minimum_flow_confidence = minimum_flow_confidence
        self._minimum_data_quality = minimum_data_quality
        self._maximum_underlying_flow_age_ms = maximum_underlying_flow_age_ms
        self._maximum_instrument_flow_age_ms = maximum_instrument_flow_age_ms
        self._maximum_quote_age_ms = maximum_quote_age_ms
        self._maximum_spread_bps = maximum_spread_bps
        self._maximum_support_age_ms = maximum_support_age_ms
        self._maximum_support_distance_percent = maximum_support_distance_percent

    @property
    def required_symbols(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                symbol
                for pair in self.pairs
                for symbol in (
                    pair.underlying_symbol,
                    pair.bullish_instrument,
                    pair.bearish_instrument,
                )
            )
        )

    def pair_for_underlying(self, symbol: str) -> LeveragedPair | None:
        normalized = symbol.strip().upper()
        return next((pair for pair in self.pairs if pair.underlying_symbol == normalized), None)

    def evaluate(self, context: LeveragedThesisContext) -> LeveragedThesisEvaluation:
        direction, direction_reasons = self._direction(context)
        instrument, exposure = self._instrument(context.pair, direction)
        instrument_flow = (
            context.instrument_flows.get(instrument) if instrument is not None else None
        )
        reasons = list(direction_reasons)
        state = LeveragedThesisState.OBSERVING
        support_gate = "NOT_EVALUATED"
        support_reasons: tuple[str, ...] = ()
        if direction is not PatternDirection.NEUTRAL:
            support_gate, support_reasons = self._support_gate(context, direction)

        if direction is PatternDirection.NEUTRAL:
            reasons.append("no_directional_order_flow")
        elif context.session is not MarketSession.REGULAR:
            state = LeveragedThesisState.BLOCKED
            reasons.append("regular_session_required")
        elif not self._flow_ready(
            context.underlying_flow,
            as_of=context.as_of,
            maximum_age_ms=self._maximum_underlying_flow_age_ms,
        ):
            state = LeveragedThesisState.BLOCKED
            reasons.append("underlying_flow_quality_or_freshness_failed")
        elif self._analysis_conflicts(context, direction):
            state = LeveragedThesisState.BLOCKED
            reasons.append("intraday_structure_conflicts_with_flow")
        elif support_gate == "PENDING":
            state = LeveragedThesisState.OBSERVING
            reasons.extend(support_reasons)
        elif support_gate in {"LONG_BLOCKED", "SHORT_BLOCKED"}:
            state = LeveragedThesisState.BLOCKED
            reasons.extend(support_reasons)
        elif not self._structure_ready(context, direction):
            state = LeveragedThesisState.EARLY_FLOW
            reasons.extend((*support_reasons, "structure_confirmation_pending"))
        elif support_gate == "LONG_CONTEXT":
            state = LeveragedThesisState.STRUCTURE_ARMED
            reasons.extend(support_reasons)
        elif instrument_flow is None or not self._flow_ready(
            instrument_flow,
            as_of=context.as_of,
            maximum_age_ms=self._maximum_instrument_flow_age_ms,
        ):
            state = LeveragedThesisState.STRUCTURE_ARMED
            reasons.append("instrument_order_flow_pending_or_stale")
        elif instrument_flow.state in _SELL_FLOW:
            state = LeveragedThesisState.BLOCKED
            reasons.append("instrument_flow_opposes_buy")
        elif not self._execution_quote_ready(instrument_flow):
            state = LeveragedThesisState.BLOCKED
            reasons.append("instrument_quote_evidence_unavailable")
        elif (
            instrument_flow.spread_bps is not None
            and instrument_flow.spread_bps > self._maximum_spread_bps
        ):
            state = LeveragedThesisState.BLOCKED
            reasons.append("instrument_spread_too_wide")
        elif instrument_flow.state in _BUY_FLOW:
            state = LeveragedThesisState.BUY_CONFIRMED
            reasons.extend(("intraday_structure_confirmed", "instrument_buy_flow_confirmed"))
        else:
            state = LeveragedThesisState.STRUCTURE_ARMED
            reasons.append("instrument_buy_flow_pending")

        if support_reasons:
            reasons.extend(support_reasons)

        digest = sha256_digest(
            {
                "pair": context.pair,
                "as_of": context.as_of,
                "state": state,
                "direction": direction,
                "instrument": instrument,
                "analysis": context.analysis,
                "underlying_flow": context.underlying_flow,
                "instrument_flow": instrument_flow,
                "support": context.support,
                "support_gate": support_gate,
            }
        )
        expires_after = {
            LeveragedThesisState.OBSERVING: timedelta(seconds=60),
            LeveragedThesisState.EARLY_FLOW: timedelta(seconds=90),
            LeveragedThesisState.STRUCTURE_ARMED: timedelta(minutes=2),
            LeveragedThesisState.BUY_CONFIRMED: timedelta(minutes=3),
            LeveragedThesisState.BLOCKED: timedelta(seconds=30),
        }[state]
        assessment = LeveragedThesisAssessment(
            assessment_id=_stable_uuid7(context.as_of, f"assessment:{digest}"),
            underlying_symbol=context.pair.underlying_symbol,
            instrument_symbol=instrument,
            occurred_at=context.as_of,
            expires_at=context.as_of + expires_after,
            engine_version=self.engine_version,
            state=state,
            direction=direction,
            exposure=exposure,
            underlying_price=context.underlying_flow.current_price,
            instrument_bid=(instrument_flow.bid_price if instrument_flow is not None else None),
            instrument_ask=(instrument_flow.ask_price if instrument_flow is not None else None),
            spread_bps=(instrument_flow.spread_bps if instrument_flow is not None else None),
            underlying_flow_state=context.underlying_flow.state,
            underlying_flow_confidence=context.underlying_flow.confidence,
            instrument_flow_state=instrument_flow.state if instrument_flow is not None else None,
            instrument_flow_confidence=(
                instrument_flow.confidence if instrument_flow is not None else None
            ),
            support_state=(context.support.state if context.support is not None else None),
            support_zone_position=(
                context.support.zone_position if context.support is not None else None
            ),
            support_zone_low=(context.support.zone_low if context.support is not None else None),
            support_zone_high=(context.support.zone_high if context.support is not None else None),
            support_invalidation=(
                context.support.invalidation if context.support is not None else None
            ),
            support_distance_percent=(
                self._support_distance_percent(
                    context.support,
                    context.underlying_flow.current_price,
                )
                if context.support is not None
                else None
            ),
            support_actionability_score=(
                context.support.actionability_score if context.support is not None else None
            ),
            structure_score=context.analysis.score if context.analysis is not None else None,
            source_analysis_id=(
                context.analysis.analysis_id if context.analysis is not None else None
            ),
            source_underlying_flow_state_id=context.underlying_flow.state_id,
            source_instrument_flow_state_id=(
                instrument_flow.state_id if instrument_flow is not None else None
            ),
            source_support_assessment_id=(
                context.support.assessment_id if context.support is not None else None
            ),
            reasons=tuple(dict.fromkeys(reasons)),
            context_hash=f"sha256:{digest}",
        )
        transition = self._transition(context, assessment, digest)
        return LeveragedThesisEvaluation(assessment=assessment, transition=transition)

    def _direction(
        self, context: LeveragedThesisContext
    ) -> tuple[PatternDirection, tuple[str, ...]]:
        flow_direction = _flow_direction(context.underlying_flow.state)
        if flow_direction is not PatternDirection.NEUTRAL:
            return flow_direction, (f"underlying_flow:{context.underlying_flow.state.value}",)
        if (
            context.analysis is not None
            and context.analysis.direction is not PatternDirection.NEUTRAL
        ):
            return context.analysis.direction, ("direction_from_intraday_structure",)
        return PatternDirection.NEUTRAL, ()

    @staticmethod
    def _instrument(
        pair: LeveragedPair, direction: PatternDirection
    ) -> tuple[str | None, LeveragedExposure]:
        if direction is PatternDirection.BULLISH:
            return pair.bullish_instrument, pair.bullish_exposure
        if direction is PatternDirection.BEARISH:
            return pair.bearish_instrument, pair.bearish_exposure
        return None, LeveragedExposure.NONE

    def _structure_ready(
        self, context: LeveragedThesisContext, direction: PatternDirection
    ) -> bool:
        analysis = context.analysis
        if analysis is None or analysis.direction is not direction:
            return False
        if _age_ms(context.as_of, analysis.as_of) > 120_000:
            return False
        metrics = {item.name: item.value for item in analysis.metrics}
        setup = str(metrics.get("setup", "no_trigger"))
        regime = str(metrics.get("intraday_regime", "range_or_transition"))
        quality = str(metrics.get("confirmation_quality", "weak"))
        setups = _BULLISH_SETUPS if direction is PatternDirection.BULLISH else _BEARISH_SETUPS
        expected_regime = (
            "bullish_trend" if direction is PatternDirection.BULLISH else "bearish_trend"
        )
        return (
            setup in setups
            and regime == expected_regime
            and quality in {"standard", "strong"}
            and analysis.score >= self._minimum_structure_score
        )

    @staticmethod
    def _analysis_conflicts(context: LeveragedThesisContext, direction: PatternDirection) -> bool:
        analysis = context.analysis
        return (
            analysis is not None
            and analysis.direction is not PatternDirection.NEUTRAL
            and analysis.direction is not direction
            and _age_ms(context.as_of, analysis.as_of) <= 120_000
        )

    def _flow_ready(self, flow: OrderFlowState, *, as_of: datetime, maximum_age_ms: int) -> bool:
        return (
            _age_ms(as_of, flow.occurred_at) <= maximum_age_ms
            and flow.quote_fresh
            and flow.confidence >= self._minimum_flow_confidence
            and flow.data_quality >= self._minimum_data_quality
        )

    def _execution_quote_ready(self, flow: OrderFlowState) -> bool:
        return (
            flow.quote_fresh
            and flow.quote_age_ms is not None
            and flow.quote_age_ms <= self._maximum_quote_age_ms
            and flow.bid_price is not None
            and flow.ask_price is not None
            and flow.spread_bps is not None
        )

    def _support_gate(
        self,
        context: LeveragedThesisContext,
        direction: PatternDirection,
    ) -> tuple[str, tuple[str, ...]]:
        support = context.support
        if support is None:
            return "PENDING", ("support_assessment_pending",)
        assessed_at = support.assessed_at or support.occurred_at
        if _age_ms(context.as_of, assessed_at) > self._maximum_support_age_ms:
            return "PENDING", ("support_assessment_stale",)
        if support.state is SupportState.EXPIRED:
            return "PENDING", ("support_assessment_expired",)

        spot = context.underlying_flow.current_price
        broken = (
            support.state is SupportState.INVALIDATED
            or support.zone_position is SupportZonePosition.BELOW_ZONE
            or (support.invalidation is not None and spot <= support.invalidation)
        )
        distance = self._support_distance_percent(support, spot)
        near = distance is not None and distance <= self._maximum_support_distance_percent
        if support.zone_distance_atr is not None:
            near = near and support.zone_distance_atr <= Decimal("1.5")

        if direction is PatternDirection.BEARISH:
            if support.state in _NO_NEARBY_SUPPORT_STATES:
                return "SHORT_CLEAR", ("no_nearby_support_for_short",)
            if broken:
                return "SHORT_CLEAR", ("support_zone_broken_for_short",)
            if near:
                return "SHORT_BLOCKED", ("nearby_support_blocks_short",)
            return "SHORT_CLEAR", ("key_support_not_near_spot",)

        if direction is not PatternDirection.BULLISH:
            return "PENDING", ("support_direction_not_evaluated",)
        if support.state in _NO_NEARBY_SUPPORT_STATES:
            return "LONG_BLOCKED", ("no_nearby_support_for_long",)
        if broken:
            return "LONG_BLOCKED", ("support_invalidated_for_long",)
        if support.b_wave_risk or support.state is SupportState.B_WAVE_RISK:
            return "LONG_BLOCKED", ("support_b_wave_risk",)
        if not near:
            return "LONG_BLOCKED", ("key_support_not_near_spot",)
        if (
            support.state in _SUPPORT_STRUCTURE_STATES
            and support.reversal_score >= Decimal("60")
            and support.actionability_score >= Decimal("55")
        ):
            return "LONG_CONFIRMED", ("support_structure_confirmed",)
        if (
            support.state in _SUPPORT_REACTION_STATES
            and support.reaction_score >= Decimal("60")
            and support.actionability_score >= Decimal("45")
        ):
            return "LONG_CONFIRMED", ("support_reaction_confirmed",)
        if (
            support.state in _SUPPORT_CONTEXT_STATES
            and len(support.support_sources) >= 2
            and support.actionability_score >= Decimal("20")
        ):
            return "LONG_CONTEXT", ("support_reaction_pending",)
        return "LONG_BLOCKED", ("support_not_actionable_for_long",)

    @staticmethod
    def _support_distance_percent(
        support: SupportAssessment,
        spot: Decimal,
    ) -> Decimal | None:
        if support.zone_high is None:
            return support.zone_distance_percent
        if spot <= support.zone_high:
            return Decimal("0")
        return ((spot - support.zone_high) / spot * Decimal("100")).quantize(Decimal("0.0001"))

    def _transition(
        self,
        context: LeveragedThesisContext,
        assessment: LeveragedThesisAssessment,
        digest: str,
    ) -> LeveragedThesisTransition | None:
        previous = context.previous_assessment
        if previous is not None and (
            previous.state is assessment.state
            and previous.instrument_symbol == assessment.instrument_symbol
            and previous.direction is assessment.direction
        ):
            return None
        return LeveragedThesisTransition(
            transition_id=_stable_uuid7(
                context.as_of,
                f"transition:{digest}:{assessment.state.value}:{assessment.instrument_symbol}",
            ),
            assessment_id=assessment.assessment_id,
            underlying_symbol=assessment.underlying_symbol,
            instrument_symbol=assessment.instrument_symbol,
            occurred_at=assessment.occurred_at,
            engine_version=self.engine_version,
            previous_state=previous.state if previous is not None else None,
            state=assessment.state,
            previous_instrument_symbol=(
                previous.instrument_symbol if previous is not None else None
            ),
            direction=assessment.direction,
            exposure=assessment.exposure,
            reference_price=assessment.underlying_price,
            reasons=assessment.reasons,
            context_hash=assessment.context_hash,
        )


def _flow_direction(kind: OrderFlowStateKind) -> PatternDirection:
    if kind in _BUY_FLOW:
        return PatternDirection.BULLISH
    if kind in _SELL_FLOW:
        return PatternDirection.BEARISH
    return PatternDirection.NEUTRAL


def _age_ms(later: datetime, earlier: datetime) -> int:
    delta = later - earlier
    if delta < timedelta(0):
        return 2**31 - 1
    return int(delta.total_seconds() * 1000)


def _stable_uuid7(occurred_at: datetime, identity: str) -> UUID:
    timestamp_ms = int(occurred_at.timestamp() * 1_000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(hashlib.sha256(identity.encode()).digest(), "big") & (
        (1 << 74) - 1
    )
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
