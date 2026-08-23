"""Re-armable multi-session structure recovery for Swing v10."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.contracts import MarketBar, NamedValue

from .models import SwingContext
from .v9 import SwingEngineV9


class SwingEngineV10(SwingEngineV9):
    """Wait for a correction-defined risk point and identify each recovery cycle."""

    engine_version = "10.0.0"

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
        recovery_selloff_lookback_days: int = 10,
        recovery_intraday_confirmation_bars: int = 4,
        recovery_intraday_breakout_lookback_bars: int = 3,
        recovery_stop_lookback_bars: int = 3,
        recovery_stop_atr_buffer: Decimal = Decimal("0.10"),
        recovery_minimum_selloff_atr: Decimal = Decimal("1"),
        recovery_maximum_risk_percent: Decimal = Decimal("12"),
        recovery_maximum_risk_atr: Decimal = Decimal("1"),
        recovery_minimum_reward_risk: Decimal = Decimal("1.5"),
        strategy_version: str = "3.2.0",
    ) -> None:
        if recovery_selloff_lookback_days < 2:
            raise ValueError("recovery_selloff_lookback_days must be at least 2")
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
            recovery_intraday_breakout_lookback_bars=(recovery_intraday_breakout_lookback_bars),
            recovery_stop_lookback_bars=recovery_stop_lookback_bars,
            recovery_stop_atr_buffer=recovery_stop_atr_buffer,
            recovery_minimum_selloff_atr=recovery_minimum_selloff_atr,
            recovery_maximum_risk_percent=recovery_maximum_risk_percent,
            recovery_maximum_risk_atr=recovery_maximum_risk_atr,
            recovery_minimum_reward_risk=recovery_minimum_reward_risk,
            strategy_version=strategy_version,
        )
        self._recovery_selloff_lookback_days = recovery_selloff_lookback_days

    def _recovery_selloff_atr(
        self,
        context: SwingContext,
        *,
        pivot_index: int,
        atr14: Decimal,
    ) -> Decimal:
        start = max(0, pivot_index - self._recovery_selloff_lookback_days)
        correction_origin = max(bar.high for bar in context.daily_bars[start:pivot_index])
        return (correction_origin - context.daily_bars[pivot_index].low) / atr14

    def _recovery_invalidation(
        self,
        context: SwingContext,
        *,
        pivot: MarketBar,
        confirmation: tuple[MarketBar, ...],
        atr14: Decimal,
    ) -> Decimal:
        del context, confirmation
        return (pivot.low - atr14 * self._recovery_stop_atr_buffer).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )

    def _recovery_invalidation_source(self) -> str:
        return "correction_pivot_low"

    def _recovery_setup_metrics(
        self,
        context: SwingContext,
        pivot: MarketBar,
    ) -> tuple[NamedValue, ...]:
        setup_id = (
            f"swing-recovery:{context.symbol}:{pivot.timestamp.isoformat().replace('+00:00', 'Z')}"
        )
        return (
            NamedValue(name="recovery_setup_id", value=setup_id),
            NamedValue(name="recovery_rearmed", value=True),
            NamedValue(
                name="recovery_selloff_lookback_days",
                value=self._recovery_selloff_lookback_days,
            ),
        )
