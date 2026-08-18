"""Assembly adapter owned by Swing business rules."""

from __future__ import annotations

from app.common.strategy import StrategySource


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation in {"3.0.0", "4.0.0", "5.0.0", "6.0.0"}:
        source.behavior().boolean("anchored_vwap_gate")
    if implementation in {"4.0.0", "5.0.0", "6.0.0"}:
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
    if implementation == "6.0.0":
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


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation in {"3.0.0", "4.0.0", "5.0.0", "6.0.0"}:
        behavior = source.behavior()
        kwargs.update(
            anchored_vwap_gate=behavior.boolean("anchored_vwap_gate"),
            strategy_version=source.version,
        )
        if implementation in {"4.0.0", "5.0.0", "6.0.0"}:
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
        if implementation == "6.0.0":
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
    return args, kwargs
