"""Assembly adapter for versioned 4HGERI v1.4 rules."""

from __future__ import annotations

from app.common.strategy import StrategySource


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in {"1.4.0", "1.5.0", "1.8.0"}:
        return
    behavior = source.behavior()
    for name in ("pivot_radius", "minimum_bars", "lookback_bars", "countertrend_ttl_sessions"):
        behavior.positive_int(name)
    for name in (
        "breakout_atr",
        "zone_atr",
        "invalidation_atr",
        "maximum_extension_atr",
        "countertrend_minimum_reward_risk",
    ):
        if behavior.decimal(name) <= 0:
            raise ValueError(f"4HGERI {name} must be positive")
    behavior.boolean("countertrend_requires_reaction")
    if implementation in {"1.5.0", "1.8.0"}:
        behavior.positive_int("support_freshness_days")
    if implementation == "1.8.0" and behavior.decimal("structural_rebase_atr") <= 0:
        raise ValueError("4HGERI structural_rebase_atr must be positive")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation not in {"1.4.0", "1.5.0", "1.8.0"}:
        return args, kwargs
    behavior = source.behavior()
    kwargs.update(
        pivot_radius=behavior.positive_int("pivot_radius"),
        minimum_bars=behavior.positive_int("minimum_bars"),
        lookback_bars=behavior.positive_int("lookback_bars"),
        breakout_atr=behavior.decimal("breakout_atr"),
        zone_atr=behavior.decimal("zone_atr"),
        invalidation_atr=behavior.decimal("invalidation_atr"),
        maximum_extension_atr=behavior.decimal("maximum_extension_atr"),
        countertrend_minimum_reward_risk=behavior.decimal("countertrend_minimum_reward_risk"),
        countertrend_ttl_sessions=behavior.positive_int("countertrend_ttl_sessions"),
        countertrend_requires_reaction=behavior.boolean("countertrend_requires_reaction"),
    )
    if implementation in {"1.5.0", "1.8.0"}:
        kwargs["support_freshness_days"] = behavior.positive_int("support_freshness_days")
    if implementation == "1.8.0":
        kwargs["structural_rebase_atr"] = behavior.decimal("structural_rebase_atr")
    return args, kwargs
