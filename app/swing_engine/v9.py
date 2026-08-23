"""Correction-anchored recovery AVWAP for Swing v9."""

from __future__ import annotations

from decimal import Decimal

from app.contracts import MarketBar, NamedValue

from .indicators import anchored_vwap, percent_vs
from .models import SwingContext
from .v8 import SwingEngineV8


class SwingEngineV9(SwingEngineV8):
    """Confirm recovery against the recent correction instead of a stale pivot."""

    engine_version = "9.0.0"

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
        strategy_version: str = "3.1.0",
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
            recovery_enabled=recovery_enabled,
            recovery_daily_lookback_days=recovery_daily_lookback_days,
            recovery_intraday_confirmation_bars=recovery_intraday_confirmation_bars,
            recovery_intraday_breakout_lookback_bars=(
                recovery_intraday_breakout_lookback_bars
            ),
            recovery_stop_lookback_bars=recovery_stop_lookback_bars,
            recovery_stop_atr_buffer=recovery_stop_atr_buffer,
            recovery_minimum_selloff_atr=recovery_minimum_selloff_atr,
            recovery_maximum_risk_percent=recovery_maximum_risk_percent,
            recovery_maximum_risk_atr=recovery_maximum_risk_atr,
            recovery_minimum_reward_risk=recovery_minimum_reward_risk,
            strategy_version=strategy_version,
        )

    def _recovery_avwap_distance(
        self,
        context: SwingContext,
        metrics: dict[str, object],
        pivot_index: int,
    ) -> Decimal:
        del metrics
        recovery_avwap = anchored_vwap(context.daily_bars, pivot_index)
        return percent_vs(context.price, recovery_avwap)

    def _recovery_avwap_reason(self) -> str:
        return "recovery_avwap_reclaimed"

    def _recovery_avwap_metrics(
        self,
        context: SwingContext,
        pivot: MarketBar,
    ) -> tuple[NamedValue, ...]:
        pivot_index = context.daily_bars.index(pivot)
        recovery_avwap = anchored_vwap(context.daily_bars, pivot_index)
        return (
            NamedValue(name="recovery_pivot_avwap_passed", value=True),
            NamedValue(name="recovery_avwap_anchor_at", value=pivot.timestamp),
            NamedValue(name="recovery_avwap", value=recovery_avwap),
            NamedValue(
                name="price_vs_recovery_avwap_percent",
                value=percent_vs(context.price, recovery_avwap),
            ),
            NamedValue(name="recovery_avwap_gate_passed", value=True),
        )
