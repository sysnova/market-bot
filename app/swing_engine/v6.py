"""Finite-state lifecycle for failed daily breakouts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, MarketBar, NamedValue

from .indicators import atr
from .models import SwingContext
from .v3 import SwingEngineV3
from .v4 import DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE

BREAKOUT_CONFIRMATION_MULTIPLIER = Decimal("1.003")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


class FailedBreakoutState(StrEnum):
    """Auditable lifecycle states emitted by Swing v6."""

    NONE = "NONE"
    ACTIVE = "ACTIVE"
    NEW_BREAKOUT_PENDING = "NEW_BREAKOUT_PENDING"
    RECOVERED = "RECOVERED"
    STRUCTURE_INVALIDATED = "STRUCTURE_INVALIDATED"
    VOLATILITY_INVALIDATED = "VOLATILITY_INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


_BLOCKING_STATES = {
    FailedBreakoutState.ACTIVE,
    FailedBreakoutState.NEW_BREAKOUT_PENDING,
}


@dataclass(frozen=True, slots=True)
class FailedBreakoutAssessment:
    """Current deterministic lifecycle assessment for the latest failed event."""

    state: FailedBreakoutState
    level: Decimal | None = None
    breakout_at: datetime | None = None
    failure_at: datetime | None = None
    resolved_at: datetime | None = None
    atr14_snapshot: Decimal | None = None
    age_bars: int | None = None
    superseding_breakout_at: datetime | None = None
    superseding_breakout_level: Decimal | None = None

    @property
    def blocks_entry(self) -> bool:
        return self.state in _BLOCKING_STATES


@dataclass(frozen=True, slots=True)
class _BreakoutEvent:
    index: int
    level: Decimal
    atr14_snapshot: Decimal
    failure_index: int | None
    confirmation_index: int | None


class SwingEngineV6(SwingEngineV3):
    """Expire or supersede dead breakout anchors without weakening entry gates."""

    engine_version = "6.0.0"

    def __init__(
        self,
        *,
        anchored_vwap_gate: bool = True,
        minimum_reward_risk_to_resistance: Decimal = (
            DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE
        ),
        structural_support_lookback_days: int = 10,
        resistance_lookback_days: int = 20,
        failed_breakout_failure_window_days: int = 5,
        failed_breakout_maximum_age_days: int = 60,
        failed_breakout_structural_reset_lookback_days: int = 20,
        failed_breakout_reset_atr_multiple: Decimal = Decimal("5"),
        strategy_version: str = "2.0.0",
    ) -> None:
        super().__init__(
            anchored_vwap_gate=anchored_vwap_gate,
            strategy_version=strategy_version,
        )
        positive_decimals = {
            "minimum_reward_risk_to_resistance": (
                minimum_reward_risk_to_resistance
            ),
            "failed_breakout_reset_atr_multiple": (
                failed_breakout_reset_atr_multiple
            ),
        }
        for name, value in positive_decimals.items():
            if value <= ZERO:
                raise ValueError(f"{name} must be positive")
        positive_integers = {
            "structural_support_lookback_days": structural_support_lookback_days,
            "resistance_lookback_days": resistance_lookback_days,
            "failed_breakout_failure_window_days": (
                failed_breakout_failure_window_days
            ),
            "failed_breakout_maximum_age_days": failed_breakout_maximum_age_days,
            "failed_breakout_structural_reset_lookback_days": (
                failed_breakout_structural_reset_lookback_days
            ),
        }
        for name, value in positive_integers.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self._minimum_reward_risk_to_resistance = (
            minimum_reward_risk_to_resistance
        )
        self._structural_support_lookback_days = structural_support_lookback_days
        self._resistance_lookback_days = resistance_lookback_days
        self._failed_breakout_failure_window_days = (
            failed_breakout_failure_window_days
        )
        self._failed_breakout_maximum_age_days = (
            failed_breakout_maximum_age_days
        )
        self._failed_breakout_structural_reset_lookback_days = (
            failed_breakout_structural_reset_lookback_days
        )
        self._failed_breakout_reset_atr_multiple = (
            failed_breakout_reset_atr_multiple
        )

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        if len(context.daily_bars) < 30:
            return result.model_copy(update={"engine_version": self.engine_version})

        metrics = _metric_map(result)
        atr14 = _required_decimal(metrics, "atr14")
        structural_support = min(
            bar.low
            for bar in context.daily_bars[-self._structural_support_lookback_days :]
        )
        invalidation = _rounded(structural_support * Decimal("0.985"))
        if invalidation >= context.price:
            invalidation = _rounded(context.price - atr14)

        resistance_bars = context.daily_bars[-(self._resistance_lookback_days + 1) : -1]
        if not resistance_bars:
            resistance_bars = context.daily_bars[-self._resistance_lookback_days :]
        body_resistance = max(bar.close for bar in resistance_bars)
        liquidity_high = max(bar.high for bar in resistance_bars)

        risk = context.price - invalidation
        risk_percent = risk / context.price * HUNDRED
        risk_atr = risk / atr14
        risk_ok = (
            risk > ZERO
            and risk_percent <= Decimal("8")
            and risk_atr <= Decimal("3")
        )
        reward_risk = (
            ZERO
            if risk <= ZERO or body_resistance <= context.price
            else (body_resistance - context.price) / risk
        )
        target_2r = _rounded(context.price + risk * Decimal("2"))

        lifecycle = _failed_breakout_lifecycle(
            context.daily_bars,
            resistance_lookback_days=self._resistance_lookback_days,
            failure_window_days=self._failed_breakout_failure_window_days,
            maximum_age_days=self._failed_breakout_maximum_age_days,
            structural_reset_lookback_days=(
                self._failed_breakout_structural_reset_lookback_days
            ),
            reset_atr_multiple=self._failed_breakout_reset_atr_multiple,
        )
        classification = str(metrics.get("classification", "setup"))
        if lifecycle.blocks_entry and classification in {"pullback", "breakout"}:
            classification = "setup"

        anchored_vwap_passed = metrics.get("anchored_vwap_gate_passed") is True
        reward_risk_passed = reward_risk >= self._minimum_reward_risk_to_resistance
        entry_gate_passed = (
            anchored_vwap_passed
            and classification in {"pullback", "breakout"}
            and risk_ok
            and reward_risk_passed
            and not lifecycle.blocks_entry
        )

        old_reward_risk = _decimal(metrics.get("reward_risk_to_resistance")) or ZERO
        score = _score(
            result.score
            - _reward_risk_score_adjustment(old_reward_risk)
            + _reward_risk_score_adjustment(reward_risk)
        )
        verdict = result.verdict
        if classification in {"pullback", "breakout"}:
            verdict = (
                AnalysisVerdict.FAVORABLE
                if entry_gate_passed and score >= Decimal("65")
                else AnalysisVerdict.WATCH
            )
        elif lifecycle.blocks_entry and verdict is not AnalysisVerdict.AVOID:
            verdict = AnalysisVerdict.WATCH
        if not entry_gate_passed and verdict is AnalysisVerdict.WATCH:
            score = min(score, Decimal("64.00"))

        reasons = list(result.reasons)
        if lifecycle.state is FailedBreakoutState.ACTIVE:
            reasons.append("failed_breakout_recovery_pending")
        elif lifecycle.state is FailedBreakoutState.NEW_BREAKOUT_PENDING:
            reasons.append("failed_breakout_new_breakout_pending")
        elif lifecycle.state not in {FailedBreakoutState.NONE}:
            reasons.append(f"failed_breakout_{lifecycle.state.value.lower()}")
        if not reward_risk_passed:
            reasons.append("insufficient_reward_risk_to_close_resistance")

        risk_flags = [
            value
            for value in _string_tuple(metrics.get("risk_flags"))
            if value != "invalidation_risk_too_wide"
        ]
        if not risk_ok:
            risk_flags.append("structural_invalidation_risk_too_wide")
        if lifecycle.blocks_entry:
            risk_flags.append("failed_breakout")

        reset_reason = (
            lifecycle.state.value
            if lifecycle.state
            not in {
                FailedBreakoutState.NONE,
                FailedBreakoutState.ACTIVE,
                FailedBreakoutState.NEW_BREAKOUT_PENDING,
            }
            else None
        )
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": verdict,
                "score": score,
                "confidence": (score / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": _upsert(
                    result,
                    NamedValue(name="classification", value=classification),
                    NamedValue(name="risk_flags", value=tuple(dict.fromkeys(risk_flags))),
                    NamedValue(name="support", value=_rounded(structural_support)),
                    NamedValue(name="structural_support", value=_rounded(structural_support)),
                    NamedValue(name="invalidation", value=invalidation),
                    NamedValue(name="invalidation_source", value="recent_daily_low"),
                    NamedValue(name="resistance", value=_rounded(body_resistance)),
                    NamedValue(name="resistance_source", value="completed_daily_closes"),
                    NamedValue(name="liquidity_high", value=_rounded(liquidity_high)),
                    NamedValue(name="target_2r", value=target_2r),
                    NamedValue(name="risk_percent", value=_rounded(risk_percent)),
                    NamedValue(name="risk_atr", value=_rounded(risk_atr)),
                    NamedValue(
                        name="reward_risk_to_resistance", value=_rounded(reward_risk)
                    ),
                    NamedValue(name="failed_breakout", value=lifecycle.blocks_entry),
                    NamedValue(name="failed_breakout_state", value=lifecycle.state.value),
                    NamedValue(
                        name="failed_breakout_level",
                        value=(
                            _rounded(lifecycle.level)
                            if lifecycle.level is not None
                            else None
                        ),
                    ),
                    NamedValue(name="failed_breakout_at", value=lifecycle.breakout_at),
                    NamedValue(
                        name="failed_breakout_failure_at", value=lifecycle.failure_at
                    ),
                    NamedValue(
                        name="failed_breakout_resolved_at", value=lifecycle.resolved_at
                    ),
                    NamedValue(
                        name="failed_breakout_atr14_snapshot",
                        value=lifecycle.atr14_snapshot,
                    ),
                    NamedValue(
                        name="failed_breakout_age_bars", value=lifecycle.age_bars
                    ),
                    NamedValue(name="failed_breakout_reset_reason", value=reset_reason),
                    NamedValue(
                        name="failed_breakout_superseding_at",
                        value=lifecycle.superseding_breakout_at,
                    ),
                    NamedValue(
                        name="failed_breakout_superseding_level",
                        value=(
                            _rounded(lifecycle.superseding_breakout_level)
                            if lifecycle.superseding_breakout_level is not None
                            else None
                        ),
                    ),
                    NamedValue(name="swing_entry_gate_passed", value=entry_gate_passed),
                    NamedValue(
                        name="minimum_reward_risk_to_resistance",
                        value=self._minimum_reward_risk_to_resistance,
                    ),
                    NamedValue(
                        name="entry_confirmation_rule_version",
                        value=self._strategy_version,
                    ),
                ),
            }
        )


def _failed_breakout_lifecycle(
    bars: tuple[MarketBar, ...],
    *,
    resistance_lookback_days: int,
    failure_window_days: int,
    maximum_age_days: int,
    structural_reset_lookback_days: int,
    reset_atr_multiple: Decimal,
) -> FailedBreakoutAssessment:
    events = _breakout_events(
        bars,
        resistance_lookback_days=resistance_lookback_days,
        failure_window_days=failure_window_days,
    )
    failed_events = tuple(event for event in events if event.failure_index is not None)
    if not failed_events:
        return FailedBreakoutAssessment(state=FailedBreakoutState.NONE)

    event = failed_events[-1]
    failure_index = event.failure_index
    if failure_index is None:  # Narrow the optional value for static analysis.
        raise AssertionError("failed event requires failure_index")

    superseding_events = tuple(
        candidate
        for candidate in events
        if candidate.index > failure_index
        and candidate.index - resistance_lookback_days >= failure_index
    )
    confirmed_by_index = {
        candidate.confirmation_index: candidate
        for candidate in superseding_events
        if candidate.confirmation_index is not None
    }

    for index in range(failure_index, len(bars)):
        current = bars[index]
        if (superseding := confirmed_by_index.get(index)) is not None:
            return _assessment_for_event(
                bars,
                event,
                state=FailedBreakoutState.SUPERSEDED,
                resolved_index=index,
                superseding=superseding,
            )
        if current.close >= event.level * BREAKOUT_CONFIRMATION_MULTIPLIER:
            return _assessment_for_event(
                bars,
                event,
                state=FailedBreakoutState.RECOVERED,
                resolved_index=index,
            )
        if index >= structural_reset_lookback_days:
            prior_low = min(
                bar.low
                for bar in bars[index - structural_reset_lookback_days : index]
            )
            if current.close < prior_low:
                return _assessment_for_event(
                    bars,
                    event,
                    state=FailedBreakoutState.STRUCTURE_INVALIDATED,
                    resolved_index=index,
                )
        volatility_floor = event.level - reset_atr_multiple * event.atr14_snapshot
        if current.close < volatility_floor:
            return _assessment_for_event(
                bars,
                event,
                state=FailedBreakoutState.VOLATILITY_INVALIDATED,
                resolved_index=index,
            )
        if index - event.index >= maximum_age_days:
            return _assessment_for_event(
                bars,
                event,
                state=FailedBreakoutState.EXPIRED,
                resolved_index=index,
            )

    pending = next(
        (
            candidate
            for candidate in reversed(superseding_events)
            if candidate.failure_index is None
            and candidate.confirmation_index is None
        ),
        None,
    )
    return _assessment_for_event(
        bars,
        event,
        state=(
            FailedBreakoutState.NEW_BREAKOUT_PENDING
            if pending is not None
            else FailedBreakoutState.ACTIVE
        ),
        superseding=pending,
    )


def _breakout_events(
    bars: tuple[MarketBar, ...],
    *,
    resistance_lookback_days: int,
    failure_window_days: int,
) -> tuple[_BreakoutEvent, ...]:
    events: list[_BreakoutEvent] = []
    for index in range(resistance_lookback_days, len(bars)):
        prior = bars[index - resistance_lookback_days : index]
        level = max(bar.high for bar in prior)
        if bars[index].close < level * BREAKOUT_CONFIRMATION_MULTIPLIER:
            continue
        following = bars[index + 1 : index + 1 + failure_window_days]
        failure_index = next(
            (
                index + offset
                for offset, bar in enumerate(following, start=1)
                if bar.close < level
            ),
            None,
        )
        confirmation_index = (
            index + failure_window_days
            if failure_index is None and len(following) == failure_window_days
            else None
        )
        events.append(
            _BreakoutEvent(
                index=index,
                level=level,
                atr14_snapshot=atr(bars[: index + 1]),
                failure_index=failure_index,
                confirmation_index=confirmation_index,
            )
        )
    return tuple(events)


def _assessment_for_event(
    bars: tuple[MarketBar, ...],
    event: _BreakoutEvent,
    *,
    state: FailedBreakoutState,
    resolved_index: int | None = None,
    superseding: _BreakoutEvent | None = None,
) -> FailedBreakoutAssessment:
    failure_index = event.failure_index
    return FailedBreakoutAssessment(
        state=state,
        level=event.level,
        breakout_at=bars[event.index].timestamp,
        failure_at=(
            bars[failure_index].timestamp if failure_index is not None else None
        ),
        resolved_at=(
            bars[resolved_index].timestamp if resolved_index is not None else None
        ),
        atr14_snapshot=event.atr14_snapshot,
        age_bars=(
            (resolved_index if resolved_index is not None else len(bars) - 1)
            - event.index
        ),
        superseding_breakout_at=(
            bars[superseding.index].timestamp if superseding is not None else None
        ),
        superseding_breakout_level=(
            superseding.level if superseding is not None else None
        ),
    )


def _reward_risk_score_adjustment(value: Decimal) -> Decimal:
    if value >= Decimal("2"):
        return Decimal("5")
    if ZERO < value < Decimal("1.5"):
        return Decimal("-10")
    return ZERO


def _metric_map(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _required_decimal(metrics: dict[str, object], name: str) -> Decimal:
    value = _decimal(metrics.get(name))
    if value is None:
        raise ValueError(f"missing decimal metric: {name}")
    return value


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    items = cast("tuple[object, ...] | list[object]", value)
    return tuple(item for item in items if isinstance(item, str))


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
