"""Pure and deterministic microstructure scalping decisions."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.common.canonical import sha256_digest
from app.contracts.order_flow import OrderFlowStateKind
from app.contracts.scalp import (
    ScalpAssessment,
    ScalpDirection,
    ScalpExitReason,
    ScalpSetup,
    ScalpState,
    ScalpTransition,
)

from .models import ScalpContext, ScalpEvaluation
from .strategy import ScalpPolicy

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")
BULLISH_FLOW = {
    OrderFlowStateKind.BUY_PRESSURE,
    OrderFlowStateKind.SELLER_EXHAUSTION,
    OrderFlowStateKind.BUY_ABSORPTION,
    OrderFlowStateKind.BULLISH_DIVERGENCE,
}
BEARISH_FLOW = {
    OrderFlowStateKind.SELL_PRESSURE,
    OrderFlowStateKind.BUYER_EXHAUSTION,
    OrderFlowStateKind.SELL_ABSORPTION,
    OrderFlowStateKind.BEARISH_DIVERGENCE,
}


class ScalpEngine:
    """Mature same-session setups without clocks, I/O, positions or broker calls."""

    engine_id = "scalp"
    engine_version = "1.0.0"

    def __init__(self, policy: ScalpPolicy | None = None) -> None:
        self._policy = policy or ScalpPolicy()

    def evaluate(self, context: ScalpContext) -> ScalpEvaluation:
        digest = sha256_digest(context.model_dump(mode="python"))
        previous = context.previous_assessment
        state, setup, exit_reason, reasons = self._next_state(context)
        if (
            previous is not None
            and previous.state not in {ScalpState.EXIT_CONFIRMED, ScalpState.INVALIDATED}
            and previous.setup is setup
            and setup is not ScalpSetup.NONE
        ):
            levels = (
                previous.entry_price,
                previous.invalidation,
                previous.target,
                previous.max_hold_seconds,
            )
        else:
            levels = self._levels(context, setup)
        entry_price, invalidation, target, max_hold_seconds = levels
        entered_at = self._entered_at(context, state)
        support_low, support_high = self._support_levels(context, setup)
        assessment = ScalpAssessment(
            assessment_id=_stable_uuid7(context.as_of, f"assessment:{digest}"),
            symbol=context.symbol,
            occurred_at=context.as_of,
            engine_version=self.engine_version,
            state=state,
            setup=setup,
            direction=self._direction(setup),
            current_price=context.current_price,
            bid_price=context.bid_price,
            ask_price=context.ask_price,
            session_vwap=context.session_vwap,
            spread_bps=self._spread_bps(context),
            order_flow_confidence=context.order_flow.confidence,
            entry_price=entry_price,
            invalidation=invalidation,
            target=target,
            max_hold_seconds=max_hold_seconds,
            support_low=support_low,
            support_high=support_high,
            entry_confirmed_at=entered_at,
            exit_reason=exit_reason,
            source_order_flow_state_id=context.order_flow.state_id,
            reasons=reasons,
            context_hash=f"sha256:{digest}",
        )
        previous_state = previous.state if previous is not None else None
        transition = None
        if previous_state is not state:
            transition = ScalpTransition(
                transition_id=_stable_uuid7(context.as_of, f"transition:{digest}:{state.value}"),
                assessment_id=assessment.assessment_id,
                symbol=context.symbol,
                occurred_at=context.as_of,
                engine_version=self.engine_version,
                previous_state=previous_state,
                state=state,
                setup=setup,
                direction=assessment.direction,
                reference_price=context.current_price,
                reasons=reasons,
                context_hash=f"sha256:{digest}",
            )
        return ScalpEvaluation(assessment=assessment, transition=transition)

    def _next_state(
        self, context: ScalpContext
    ) -> tuple[ScalpState, ScalpSetup, ScalpExitReason | None, tuple[str, ...]]:
        previous = context.previous_assessment
        if previous is not None and previous.state in {
            ScalpState.EXIT_CONFIRMED,
            ScalpState.INVALIDATED,
        }:
            elapsed = context.as_of - previous.occurred_at
            if elapsed < timedelta(seconds=self._policy.rearm_cooldown_seconds):
                return (
                    previous.state,
                    previous.setup,
                    previous.exit_reason,
                    ("terminal_state_cooldown",),
                )

        if previous is not None and previous.state in {
            ScalpState.ENTRY_CONFIRMED,
            ScalpState.MANAGING,
        }:
            exit_reason = self._exit_reason(context, previous)
            if exit_reason is not None:
                return (
                    ScalpState.EXIT_CONFIRMED,
                    previous.setup,
                    exit_reason,
                    (f"exit:{exit_reason.value.lower()}",),
                )
            return ScalpState.MANAGING, previous.setup, None, ("entry_management_active",)

        if previous is not None and previous.state is ScalpState.ARMED:
            if self._setup_invalidated(context, previous):
                return (
                    ScalpState.INVALIDATED,
                    previous.setup,
                    ScalpExitReason.SETUP_INVALIDATED,
                    ("setup_invalidation_reached_before_entry",),
                )
            gate_failures = self._gate_failures(context)
            if gate_failures:
                return ScalpState.ARMED, previous.setup, None, gate_failures
            if self._entry_confirmed(context, previous.setup):
                return (
                    ScalpState.ENTRY_CONFIRMED,
                    previous.setup,
                    None,
                    ("order_flow_entry_confirmed",),
                )
            return ScalpState.ARMED, previous.setup, None, ("awaiting_order_flow_confirmation",)

        setup = self._candidate_setup(context)
        gate_failures = self._gate_failures(context)
        if setup is ScalpSetup.NONE:
            return ScalpState.WATCHING, ScalpSetup.NONE, None, ("no_intraday_setup",)
        if gate_failures:
            return ScalpState.WATCHING, ScalpSetup.NONE, None, gate_failures
        return ScalpState.ARMED, setup, None, (f"{setup.value.lower()}_armed",)

    def _candidate_setup(self, context: ScalpContext) -> ScalpSetup:
        if context.support_low is not None and context.support_high is not None:
            upper = context.support_high + context.atr * self._policy.support_tolerance_atr
            if context.support_low <= context.current_price <= upper:
                return ScalpSetup.SUPPORT_REVERSAL
        if context.previous_price <= context.session_vwap < context.current_price:
            return ScalpSetup.VWAP_RECLAIM
        if context.previous_price >= context.session_vwap > context.current_price:
            return ScalpSetup.VWAP_REJECTION
        return ScalpSetup.NONE

    def _gate_failures(self, context: ScalpContext) -> tuple[str, ...]:
        flow = context.order_flow
        reasons: list[str] = []
        if self._flow_age_ms(context) > Decimal(self._policy.max_order_flow_age_ms):
            reasons.append("order_flow_stale")
        if not flow.quote_fresh:
            reasons.append("quote_not_fresh")
        if flow.quote_age_ms is None or flow.quote_age_ms > self._policy.max_quote_age_ms:
            reasons.append("quote_age_exceeded")
        if self._spread_bps(context) > self._policy.max_spread_bps:
            reasons.append("spread_too_wide")
        if flow.confidence < self._policy.minimum_order_flow_confidence:
            reasons.append("order_flow_confidence_low")
        if flow.data_quality < self._policy.minimum_data_quality:
            reasons.append("order_flow_data_quality_low")
        if flow.unknown_trade_ratio > self._policy.maximum_unknown_trade_ratio:
            reasons.append("unknown_trade_ratio_high")
        return tuple(reasons)

    def _entry_confirmed(self, context: ScalpContext, setup: ScalpSetup) -> bool:
        if setup is ScalpSetup.VWAP_REJECTION:
            return (
                context.order_flow.state in BEARISH_FLOW
                and context.current_price <= context.previous_price
                and context.current_price < context.session_vwap
            )
        if (
            context.order_flow.state not in BULLISH_FLOW
            or context.current_price < context.previous_price
        ):
            return False
        if setup is ScalpSetup.VWAP_RECLAIM:
            return context.current_price > context.session_vwap
        if setup is ScalpSetup.SUPPORT_REVERSAL and context.support_low is not None:
            return context.current_price >= context.support_low
        return False

    def _levels(
        self, context: ScalpContext, setup: ScalpSetup
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None, int | None]:
        if setup is ScalpSetup.NONE:
            return None, None, None, None
        if setup is ScalpSetup.VWAP_REJECTION:
            entry = context.bid_price
            invalidation = max(
                context.previous_price,
                context.session_vwap + context.atr * self._policy.invalidation_atr,
            )
            if invalidation <= entry:
                invalidation = entry + context.atr * self._policy.invalidation_atr
            risk = invalidation - entry
            target = entry - risk * self._policy.reward_risk_ratio
            if target <= ZERO:
                target = entry * Decimal("0.995")
            return (
                _price(entry),
                _price(invalidation),
                _price(target),
                self._policy.max_hold_seconds,
            )
        entry = context.ask_price
        if setup is ScalpSetup.SUPPORT_REVERSAL:
            assert context.support_low is not None
            invalidation = context.support_low - context.atr * self._policy.invalidation_atr
        else:
            invalidation = min(
                context.previous_price,
                context.session_vwap - context.atr * self._policy.invalidation_atr,
            )
        if invalidation <= ZERO:
            invalidation = entry * Decimal("0.995")
        if invalidation >= entry:
            invalidation = entry - context.atr * self._policy.invalidation_atr
        risk = entry - invalidation
        target = entry + risk * self._policy.reward_risk_ratio
        return (
            _price(entry),
            _price(invalidation),
            _price(target),
            self._policy.max_hold_seconds,
        )

    @staticmethod
    def _support_levels(
        context: ScalpContext, setup: ScalpSetup
    ) -> tuple[Decimal | None, Decimal | None]:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.state not in {ScalpState.EXIT_CONFIRMED, ScalpState.INVALIDATED}
            and previous.setup is setup
        ):
            return previous.support_low, previous.support_high
        if setup is ScalpSetup.SUPPORT_REVERSAL:
            return context.support_low, context.support_high
        return None, None

    @staticmethod
    def _entered_at(context: ScalpContext, state: ScalpState) -> datetime | None:
        if state not in {
            ScalpState.ENTRY_CONFIRMED,
            ScalpState.MANAGING,
            ScalpState.EXIT_CONFIRMED,
        }:
            return None
        previous = context.previous_assessment
        if previous is not None and previous.entry_confirmed_at is not None:
            return previous.entry_confirmed_at
        return context.as_of

    def _exit_reason(
        self, context: ScalpContext, previous: ScalpAssessment
    ) -> ScalpExitReason | None:
        assert previous.invalidation is not None
        assert previous.target is not None
        if previous.direction is ScalpDirection.LONG:
            if context.current_price <= previous.invalidation:
                return ScalpExitReason.STOP
            if context.current_price >= previous.target:
                return ScalpExitReason.TARGET
        else:
            if context.current_price >= previous.invalidation:
                return ScalpExitReason.STOP
            if context.current_price <= previous.target:
                return ScalpExitReason.TARGET
        if previous.entry_confirmed_at is not None and previous.max_hold_seconds is not None:
            elapsed = context.as_of - previous.entry_confirmed_at
            if elapsed.days >= 1 or elapsed.seconds >= previous.max_hold_seconds:
                return ScalpExitReason.MAX_HOLD
        flow = context.order_flow
        reversal_states = (
            BEARISH_FLOW
            if previous.direction is ScalpDirection.LONG
            else BULLISH_FLOW
        )
        if (
            flow.state in reversal_states
            and flow.confidence >= self._policy.reversal_confidence
            and not self._gate_failures(context)
        ):
            return ScalpExitReason.ORDER_FLOW_REVERSAL
        return None

    @staticmethod
    def _setup_invalidated(context: ScalpContext, previous: ScalpAssessment) -> bool:
        assert previous.invalidation is not None
        assert previous.target is not None
        if previous.direction is ScalpDirection.LONG:
            return (
                context.current_price <= previous.invalidation
                or context.current_price >= previous.target
            )
        return (
            context.current_price >= previous.invalidation
            or context.current_price <= previous.target
        )

    @staticmethod
    def _direction(setup: ScalpSetup) -> ScalpDirection:
        if setup is ScalpSetup.NONE:
            return ScalpDirection.NONE
        if setup is ScalpSetup.VWAP_REJECTION:
            return ScalpDirection.SHORT
        return ScalpDirection.LONG

    @staticmethod
    def _spread_bps(context: ScalpContext) -> Decimal:
        midpoint = (context.bid_price + context.ask_price) / Decimal("2")
        return ((context.ask_price - context.bid_price) / midpoint * TEN_THOUSAND).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def _flow_age_ms(context: ScalpContext) -> Decimal:
        elapsed = context.as_of - context.order_flow.occurred_at
        seconds = elapsed.days * 86_400 + elapsed.seconds
        return Decimal(seconds * 1_000) + Decimal(elapsed.microseconds) / Decimal("1000")


def _price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


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
