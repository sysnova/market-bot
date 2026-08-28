"""Assembly adapter owned by Intraday business rules."""

from __future__ import annotations

from app.common.strategy import StrategySource

_V3_FIELDS = (
    "minimum_momentum_percent",
    "minimum_risk_percent",
    "minimum_atr_risk_multiple",
    "reward_risk_ratio",
)


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in {"3.0.0", "4.0.0", "5.0.0", "6.0.0"}:
        return
    behavior = source.behavior()
    for key in _V3_FIELDS:
        behavior.decimal(key)
    if implementation in {"4.0.0", "5.0.0", "6.0.0"}:
        behavior.decimal("maximum_trigger_extension_atr")
        behavior.decimal("maximum_ema20_extension_atr")
        behavior.boolean("strong_confirmation_required")
        behavior.boolean("five_minute_higher_low_required")
    if implementation in {"5.0.0", "6.0.0"}:
        behavior.boolean("short_confirmation_enabled")
        behavior.boolean("five_minute_lower_high_required")
    if implementation == "6.0.0":
        behavior.boolean("short_ema20_extension_hard_gate")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation not in {"3.0.0", "4.0.0", "5.0.0", "6.0.0"}:
        return args, kwargs
    behavior = source.behavior()
    kwargs.update(
        minimum_momentum_percent=behavior.decimal("minimum_momentum_percent"),
        minimum_risk_percent=behavior.decimal("minimum_risk_percent"),
        minimum_atr_risk_multiple=behavior.decimal("minimum_atr_risk_multiple"),
        reward_risk_ratio=behavior.decimal("reward_risk_ratio"),
        strategy_version=source.version,
    )
    if implementation in {"4.0.0", "5.0.0", "6.0.0"}:
        kwargs.update(
            maximum_trigger_extension_atr=behavior.decimal(
                "maximum_trigger_extension_atr"
            ),
            maximum_ema20_extension_atr=behavior.decimal(
                "maximum_ema20_extension_atr"
            ),
            strong_confirmation_required=behavior.boolean(
                "strong_confirmation_required"
            ),
            five_minute_higher_low_required=behavior.boolean(
                "five_minute_higher_low_required"
            ),
        )
    if implementation in {"5.0.0", "6.0.0"}:
        kwargs.update(
            short_confirmation_enabled=behavior.boolean(
                "short_confirmation_enabled"
            ),
            five_minute_lower_high_required=behavior.boolean(
                "five_minute_lower_high_required"
            ),
        )
    if implementation == "6.0.0":
        kwargs.update(
            short_ema20_extension_hard_gate=behavior.boolean(
                "short_ema20_extension_hard_gate"
            ),
        )
    return args, kwargs
