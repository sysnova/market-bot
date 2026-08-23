"""Independent structure-recovery entry lane for Swing v8."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import (
    AnalysisResult,
    AnalysisVerdict,
    MarketBar,
    NamedValue,
    PatternDirection,
)

from .models import SwingContext
from .v7 import SwingEngineV7

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_NEW_YORK = ZoneInfo("America/New_York")


class SwingEngineV8(SwingEngineV7):
    """Preserve trend continuation and add confirmed recovery after structural damage."""

    engine_version = "8.0.0"

    def __init__(
        self,
        *,
        anchored_vwap_gate: bool = True,
        minimum_reward_risk_to_resistance: Decimal = Decimal("1.5"),
        structural_support_lookback_days: int = 10,
        resistance_lookback_days: int = 20,
        failed_breakout_failure_window_days: int = 5,
        failed_breakout_maximum_age_days: int = 60,
        failed_breakout_structural_reset_lookback_days: int = 20,
        failed_breakout_reset_atr_multiple: Decimal = Decimal("5"),
        recovery_enabled: bool = True,
        recovery_daily_lookback_days: int = 5,
        recovery_intraday_confirmation_bars: int = 4,
        recovery_intraday_breakout_lookback_bars: int = 3,
        recovery_stop_lookback_bars: int = 3,
        recovery_stop_atr_buffer: Decimal = Decimal("0.10"),
        recovery_minimum_selloff_atr: Decimal = Decimal("1"),
        recovery_maximum_risk_percent: Decimal = Decimal("8"),
        recovery_maximum_risk_atr: Decimal = Decimal("1"),
        recovery_minimum_reward_risk: Decimal = Decimal("1.5"),
        strategy_version: str = "3.0.0",
    ) -> None:
        super().__init__(
            anchored_vwap_gate=anchored_vwap_gate,
            minimum_reward_risk_to_resistance=minimum_reward_risk_to_resistance,
            structural_support_lookback_days=structural_support_lookback_days,
            resistance_lookback_days=resistance_lookback_days,
            failed_breakout_failure_window_days=failed_breakout_failure_window_days,
            failed_breakout_maximum_age_days=failed_breakout_maximum_age_days,
            failed_breakout_structural_reset_lookback_days=(
                failed_breakout_structural_reset_lookback_days
            ),
            failed_breakout_reset_atr_multiple=failed_breakout_reset_atr_multiple,
            strategy_version=strategy_version,
        )
        positive_decimals = {
            "recovery_stop_atr_buffer": recovery_stop_atr_buffer,
            "recovery_minimum_selloff_atr": recovery_minimum_selloff_atr,
            "recovery_maximum_risk_percent": recovery_maximum_risk_percent,
            "recovery_maximum_risk_atr": recovery_maximum_risk_atr,
            "recovery_minimum_reward_risk": recovery_minimum_reward_risk,
        }
        for name, value in positive_decimals.items():
            if not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be finite and positive")
        positive_integers = {
            "recovery_daily_lookback_days": recovery_daily_lookback_days,
            "recovery_intraday_confirmation_bars": (
                recovery_intraday_confirmation_bars
            ),
            "recovery_intraday_breakout_lookback_bars": (
                recovery_intraday_breakout_lookback_bars
            ),
            "recovery_stop_lookback_bars": recovery_stop_lookback_bars,
        }
        for name, value in positive_integers.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if recovery_daily_lookback_days < 3:
            raise ValueError("recovery_daily_lookback_days must be at least 3")
        if recovery_intraday_confirmation_bars < 3:
            raise ValueError("recovery_intraday_confirmation_bars must be at least 3")
        if (
            recovery_intraday_breakout_lookback_bars
            >= recovery_intraday_confirmation_bars
        ):
            raise ValueError(
                "recovery_intraday_breakout_lookback_bars must be smaller than "
                "recovery_intraday_confirmation_bars"
            )
        if recovery_stop_lookback_bars > recovery_intraday_confirmation_bars:
            raise ValueError(
                "recovery_stop_lookback_bars cannot exceed "
                "recovery_intraday_confirmation_bars"
            )
        self._recovery_enabled = recovery_enabled
        self._recovery_daily_lookback_days = recovery_daily_lookback_days
        self._recovery_intraday_confirmation_bars = (
            recovery_intraday_confirmation_bars
        )
        self._recovery_intraday_breakout_lookback_bars = (
            recovery_intraday_breakout_lookback_bars
        )
        self._recovery_stop_lookback_bars = recovery_stop_lookback_bars
        self._recovery_stop_atr_buffer = recovery_stop_atr_buffer
        self._recovery_minimum_selloff_atr = recovery_minimum_selloff_atr
        self._recovery_maximum_risk_percent = recovery_maximum_risk_percent
        self._recovery_maximum_risk_atr = recovery_maximum_risk_atr
        self._recovery_minimum_reward_risk = recovery_minimum_reward_risk

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metric_map(result)
        continuation_passed = metrics.get("swing_entry_gate_passed") is True
        if continuation_passed:
            return result.model_copy(
                update={
                    "metrics": _upsert(
                        result,
                        NamedValue(name="entry_lane", value="TREND_CONTINUATION"),
                        NamedValue(
                            name="continuation_entry_gate_passed",
                            value=True,
                        ),
                        NamedValue(name="recovery_entry_gate_passed", value=False),
                    )
                }
            )

        recovery = self._recovery_assessment(context, metrics)
        common_metrics = (
            NamedValue(name="entry_lane", value="NONE"),
            NamedValue(name="continuation_entry_gate_passed", value=False),
            NamedValue(name="recovery_entry_gate_passed", value=False),
        )
        if recovery is None:
            return result.model_copy(update={"metrics": _upsert(result, *common_metrics)})

        tactical_invalidation, resistance, risk_percent, risk_atr, reward_risk, pivot = (
            recovery
        )
        recovery_score = Decimal("65")
        if reward_risk >= Decimal("2"):
            recovery_score += Decimal("5")
        if context.price > context.daily_bars[-1].high:
            recovery_score += Decimal("5")
        recovery_score = max(result.score, recovery_score)
        risk_flags = tuple(
            dict.fromkeys((*_string_tuple(metrics.get("risk_flags")), "recovery_structure_damaged"))
        )
        risk = context.price - tactical_invalidation
        target_2r = _rounded(context.price + risk * Decimal("2"))
        structural_invalidation = _decimal(metrics.get("invalidation"))
        structural_risk_percent = _decimal(metrics.get("risk_percent"))
        structural_risk_atr = _decimal(metrics.get("risk_atr"))
        return result.model_copy(
            update={
                "verdict": AnalysisVerdict.FAVORABLE,
                "direction": PatternDirection.BULLISH,
                "score": recovery_score,
                "confidence": (recovery_score / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *result.reasons,
                            "structure_recovery_confirmed",
                            "daily_rejection_and_higher_low_confirmed",
                            "pivot_low_avwap_reclaimed",
                            "intraday_recovery_breakout_confirmed",
                            "tactical_invalidation_reward_risk_confirmed",
                        )
                    )
                ),
                "metrics": _upsert(
                    result,
                    NamedValue(name="classification", value="recovery"),
                    NamedValue(name="risk_flags", value=risk_flags),
                    NamedValue(name="entry_lane", value="STRUCTURE_RECOVERY"),
                    NamedValue(name="continuation_entry_gate_passed", value=False),
                    NamedValue(name="recovery_entry_gate_passed", value=True),
                    NamedValue(name="swing_entry_gate_passed", value=True),
                    NamedValue(
                        name="structural_invalidation", value=structural_invalidation
                    ),
                    NamedValue(
                        name="structural_risk_percent", value=structural_risk_percent
                    ),
                    NamedValue(name="structural_risk_atr", value=structural_risk_atr),
                    NamedValue(name="invalidation", value=tactical_invalidation),
                    NamedValue(
                        name="invalidation_source", value="intraday_recovery_low"
                    ),
                    NamedValue(name="risk_percent", value=_rounded(risk_percent)),
                    NamedValue(name="risk_atr", value=_rounded(risk_atr)),
                    NamedValue(
                        name="reward_risk_to_resistance",
                        value=_rounded(reward_risk),
                    ),
                    NamedValue(name="target_2r", value=target_2r),
                    NamedValue(name="recovery_target", value=_rounded(resistance)),
                    NamedValue(name="recovery_pivot_at", value=pivot.timestamp),
                    NamedValue(name="recovery_reaction_low", value=_rounded(pivot.low)),
                    NamedValue(name="recovery_daily_higher_low", value=True),
                    NamedValue(name="recovery_pivot_avwap_passed", value=True),
                    NamedValue(name="recovery_intraday_higher_low", value=True),
                    NamedValue(name="recovery_intraday_breakout", value=True),
                    NamedValue(name="recovery_intraday_vwap_passed", value=True),
                    NamedValue(
                        name="recovery_minimum_reward_risk",
                        value=self._recovery_minimum_reward_risk,
                    ),
                ),
            }
        )

    def _recovery_assessment(
        self,
        context: SwingContext,
        metrics: dict[str, object],
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, MarketBar] | None:
        if (
            not self._recovery_enabled
            or len(context.daily_bars) < 50
            or len(context.intraday_bars) < 21
            or metrics.get("failed_breakout") is True
        ):
            return None
        atr14 = _decimal(metrics.get("atr14"))
        sma20 = _decimal(metrics.get("daily_sma20"))
        resistance = _decimal(metrics.get("resistance"))
        pivot_avwap_distance = _decimal(
            metrics.get("price_vs_pivot_low_avwap_percent")
        )
        if atr14 is None or sma20 is None or resistance is None:
            return None
        flags = _string_tuple(metrics.get("risk_flags"))
        damaged_structure = bool(
            context.price < sma20
            or metrics.get("structure_broken_confirmed") is True
            or "broken_daily_structure" in flags
        )
        if not damaged_structure or pivot_avwap_distance is None or pivot_avwap_distance < ZERO:
            return None

        start = max(1, len(context.daily_bars) - self._recovery_daily_lookback_days)
        candidates = context.daily_bars[start:]
        pivot_offset = min(range(len(candidates)), key=lambda index: candidates[index].low)
        pivot_index = start + pivot_offset
        if pivot_index >= len(context.daily_bars) - 1:
            return None
        pivot = context.daily_bars[pivot_index]
        latest_daily = context.daily_bars[-1]
        selloff_atr = (context.daily_bars[pivot_index - 1].close - pivot.low) / atr14
        daily_recovery = (
            selloff_atr >= self._recovery_minimum_selloff_atr
            and latest_daily.low > pivot.low
            and latest_daily.close > pivot.close
            and context.price > latest_daily.close
        )
        if not daily_recovery:
            return None

        session = _latest_session(context.intraday_bars)
        if len(session) < self._recovery_intraday_confirmation_bars:
            return None
        confirmation = session[-self._recovery_intraday_confirmation_bars :]
        current = confirmation[-1]
        breakout_bars = confirmation[
            -(self._recovery_intraday_breakout_lookback_bars + 1) : -1
        ]
        split = len(confirmation) // 2
        earlier = confirmation[:split]
        recent = confirmation[split:]
        higher_low = min(bar.low for bar in recent) > min(bar.low for bar in earlier)
        breakout = current.close > max(bar.high for bar in breakout_bars)
        vwap_passed = current.vwap is not None and current.close >= current.vwap
        if not (
            higher_low
            and breakout
            and vwap_passed
            and current.close > current.open
            and context.price == current.close
        ):
            return None

        stop_bars = confirmation[-self._recovery_stop_lookback_bars :]
        tactical_invalidation = _rounded(
            min(bar.low for bar in stop_bars) - atr14 * self._recovery_stop_atr_buffer
        )
        risk = context.price - tactical_invalidation
        if risk <= ZERO or resistance <= context.price:
            return None
        risk_percent = risk / context.price * HUNDRED
        risk_atr = risk / atr14
        reward_risk = (resistance - context.price) / risk
        if (
            risk_percent > self._recovery_maximum_risk_percent
            or risk_atr > self._recovery_maximum_risk_atr
            or reward_risk < self._recovery_minimum_reward_risk
        ):
            return None
        return (
            tactical_invalidation,
            resistance,
            risk_percent,
            risk_atr,
            reward_risk,
            pivot,
        )


def _latest_session(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
    latest_date = _market_date(bars[-1].timestamp)
    return tuple(bar for bar in bars if _market_date(bar.timestamp) == latest_date)


def _market_date(value: datetime) -> date:
    return value.astimezone(_NEW_YORK).date()


def _metric_map(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    items = cast("tuple[object, ...] | list[object]", value)
    return tuple(item for item in items if isinstance(item, str))


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
