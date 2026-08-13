"""Structural-support and close-resistance Swing entries."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, MarketBar, NamedValue

from .models import SwingContext
from .v3 import SwingEngineV3
from .v4 import DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class SwingEngineV5(SwingEngineV3):
    """Keep AVWAP as confluence while deriving risk from traded structure."""

    engine_version = "5.0.0"

    def __init__(
        self,
        *,
        anchored_vwap_gate: bool = True,
        minimum_reward_risk_to_resistance: Decimal = (
            DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE
        ),
        structural_support_lookback_days: int = 10,
        resistance_lookback_days: int = 20,
        failed_breakout_window_days: int = 5,
        strategy_version: str = "5.0.0",
    ) -> None:
        super().__init__(
            anchored_vwap_gate=anchored_vwap_gate,
            strategy_version=strategy_version,
        )
        if minimum_reward_risk_to_resistance <= ZERO:
            raise ValueError("minimum_reward_risk_to_resistance must be positive")
        if structural_support_lookback_days <= 0:
            raise ValueError("structural_support_lookback_days must be positive")
        if resistance_lookback_days <= 0:
            raise ValueError("resistance_lookback_days must be positive")
        if failed_breakout_window_days <= 0:
            raise ValueError("failed_breakout_window_days must be positive")
        self._minimum_reward_risk_to_resistance = minimum_reward_risk_to_resistance
        self._structural_support_lookback_days = structural_support_lookback_days
        self._resistance_lookback_days = resistance_lookback_days
        self._failed_breakout_window_days = failed_breakout_window_days

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

        failed_breakout, failed_level, failed_at = _failed_breakout(
            context.daily_bars,
            resistance_lookback_days=self._resistance_lookback_days,
            failure_window_days=self._failed_breakout_window_days,
        )
        classification = str(metrics.get("classification", "setup"))
        if failed_breakout and classification in {"pullback", "breakout"}:
            classification = "setup"

        anchored_vwap_passed = metrics.get("anchored_vwap_gate_passed") is True
        reward_risk_passed = reward_risk >= self._minimum_reward_risk_to_resistance
        entry_gate_passed = (
            anchored_vwap_passed
            and classification in {"pullback", "breakout"}
            and risk_ok
            and reward_risk_passed
            and not failed_breakout
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
        elif failed_breakout and verdict is not AnalysisVerdict.AVOID:
            verdict = AnalysisVerdict.WATCH
        if not entry_gate_passed and verdict is AnalysisVerdict.WATCH:
            score = min(score, Decimal("64.00"))

        reasons = list(result.reasons)
        if failed_breakout:
            reasons.append("failed_breakout_recovery_pending")
        if not reward_risk_passed:
            reasons.append("insufficient_reward_risk_to_close_resistance")
        risk_flags = [
            value
            for value in _string_tuple(metrics.get("risk_flags"))
            if value != "invalidation_risk_too_wide"
        ]
        if not risk_ok:
            risk_flags.append("structural_invalidation_risk_too_wide")
        if failed_breakout:
            risk_flags.append("failed_breakout")

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
                    NamedValue(name="failed_breakout", value=failed_breakout),
                    NamedValue(
                        name="failed_breakout_level",
                        value=_rounded(failed_level) if failed_level is not None else None,
                    ),
                    NamedValue(name="failed_breakout_at", value=failed_at),
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


def _failed_breakout(
    bars: tuple[MarketBar, ...],
    *,
    resistance_lookback_days: int,
    failure_window_days: int,
) -> tuple[bool, Decimal | None, datetime | None]:
    if len(bars) <= resistance_lookback_days:
        return False, None, None
    start = max(resistance_lookback_days, len(bars) - 60)
    for index in range(len(bars) - 2, start - 1, -1):
        prior = bars[index - resistance_lookback_days : index]
        breakout_level = max(bar.high for bar in prior)
        breakout = bars[index]
        if breakout.close < breakout_level * Decimal("1.003"):
            continue
        failure_bars = bars[index + 1 : index + 1 + failure_window_days]
        if not any(bar.close < breakout_level for bar in failure_bars):
            continue
        recovered = bars[-1].close >= breakout_level * Decimal("1.003")
        return not recovered, breakout_level, breakout.timestamp
    return False, None, None


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
