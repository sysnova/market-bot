"""Assembly adapter owned by Swing business rules."""

from __future__ import annotations

from app.common.strategy import StrategySource


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation in {"3.0.0", "4.0.0", "5.0.0", "6.0.0", "7.0.0", "8.0.0"}:
        source.behavior().boolean("anchored_vwap_gate")
    if implementation in {"4.0.0", "5.0.0", "6.0.0", "7.0.0", "8.0.0"}:
        minimum = source.behavior().decimal("minimum_reward_risk_to_resistance")
        if minimum <= 0:
            raise ValueError(
                "strategy behavior minimum_reward_risk_to_resistance must be positive"
            )
    if implementation == "5.0.0":
        behavior = source.behavior()
        behavior.positive_int("structural_support_lookback_days")
        behavior.positive_int("resistance_lookback_days")
        behavior.positive_int("failed_breakout_window_days")
    if implementation in {"6.0.0", "7.0.0", "8.0.0"}:
        behavior = source.behavior()
        behavior.positive_int("structural_support_lookback_days")
        behavior.positive_int("resistance_lookback_days")
        behavior.positive_int("failed_breakout_failure_window_days")
        behavior.positive_int("failed_breakout_maximum_age_days")
        behavior.positive_int("failed_breakout_structural_reset_lookback_days")
        if behavior.decimal("failed_breakout_reset_atr_multiple") <= 0:
            raise ValueError(
                "strategy behavior failed_breakout_reset_atr_multiple must be positive"
            )
    if implementation == "8.0.0":
        behavior = source.behavior()
        behavior.boolean("recovery_enabled")
        behavior.positive_int("recovery_daily_lookback_days")
        behavior.positive_int("recovery_intraday_confirmation_bars")
        behavior.positive_int("recovery_intraday_breakout_lookback_bars")
        behavior.positive_int("recovery_stop_lookback_bars")
        for name in (
            "recovery_stop_atr_buffer",
            "recovery_minimum_selloff_atr",
            "recovery_maximum_risk_percent",
            "recovery_maximum_risk_atr",
            "recovery_minimum_reward_risk",
        ):
            if behavior.decimal(name) <= 0:
                raise ValueError(f"strategy behavior {name} must be positive")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation in {"3.0.0", "4.0.0", "5.0.0", "6.0.0", "7.0.0", "8.0.0"}:
        behavior = source.behavior()
        kwargs.update(
            anchored_vwap_gate=behavior.boolean("anchored_vwap_gate"),
            strategy_version=source.version,
        )
        if implementation in {"4.0.0", "5.0.0", "6.0.0", "7.0.0", "8.0.0"}:
            kwargs["minimum_reward_risk_to_resistance"] = behavior.decimal(
                "minimum_reward_risk_to_resistance"
            )
        if implementation == "5.0.0":
            kwargs.update(
                structural_support_lookback_days=behavior.positive_int(
                    "structural_support_lookback_days"
                ),
                resistance_lookback_days=behavior.positive_int(
                    "resistance_lookback_days"
                ),
                failed_breakout_window_days=behavior.positive_int(
                    "failed_breakout_window_days"
                ),
            )
        if implementation in {"6.0.0", "7.0.0", "8.0.0"}:
            kwargs.update(
                structural_support_lookback_days=behavior.positive_int(
                    "structural_support_lookback_days"
                ),
                resistance_lookback_days=behavior.positive_int(
                    "resistance_lookback_days"
                ),
                failed_breakout_failure_window_days=behavior.positive_int(
                    "failed_breakout_failure_window_days"
                ),
                failed_breakout_maximum_age_days=behavior.positive_int(
                    "failed_breakout_maximum_age_days"
                ),
                failed_breakout_structural_reset_lookback_days=behavior.positive_int(
                    "failed_breakout_structural_reset_lookback_days"
                ),
                failed_breakout_reset_atr_multiple=behavior.decimal(
                    "failed_breakout_reset_atr_multiple"
                ),
            )
        if implementation == "8.0.0":
            kwargs.update(
                recovery_enabled=behavior.boolean("recovery_enabled"),
                recovery_daily_lookback_days=behavior.positive_int(
                    "recovery_daily_lookback_days"
                ),
                recovery_intraday_confirmation_bars=behavior.positive_int(
                    "recovery_intraday_confirmation_bars"
                ),
                recovery_intraday_breakout_lookback_bars=behavior.positive_int(
                    "recovery_intraday_breakout_lookback_bars"
                ),
                recovery_stop_lookback_bars=behavior.positive_int(
                    "recovery_stop_lookback_bars"
                ),
                recovery_stop_atr_buffer=behavior.decimal(
                    "recovery_stop_atr_buffer"
                ),
                recovery_minimum_selloff_atr=behavior.decimal(
                    "recovery_minimum_selloff_atr"
                ),
                recovery_maximum_risk_percent=behavior.decimal(
                    "recovery_maximum_risk_percent"
                ),
                recovery_maximum_risk_atr=behavior.decimal(
                    "recovery_maximum_risk_atr"
                ),
                recovery_minimum_reward_risk=behavior.decimal(
                    "recovery_minimum_reward_risk"
                ),
            )
    return args, kwargs
