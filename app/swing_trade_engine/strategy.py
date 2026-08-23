"""Assembly adapter for the versioned SwingTrade rule artifact."""

from __future__ import annotations

from app.common.strategy import StrategySource


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in {"1.0.0", "1.1.0", "1.2.0"}:
        return
    behavior = source.behavior()
    for name in (
        "fibonacci_lookback_sessions",
        "movement_lookback_sessions",
        "geri_freshness_sessions",
        "tracking_ttl_sessions",
        "trade_ttl_sessions",
    ):
        behavior.positive_int(name)
    for name in (
        "fibonacci_50_ratio",
        "fibonacci_618_ratio",
        "fibonacci_1618_ratio",
        "support_band_atr",
        "invalidation_atr",
        "minimum_reward_risk",
        "maximum_distance_to_zone_atr",
    ):
        if behavior.decimal(name) <= 0:
            raise ValueError(f"SwingTrade {name} must be positive")
    if implementation in {"1.1.0", "1.2.0"}:
        behavior.positive_int("minimum_rvol_samples")
        if behavior.decimal("minimum_intraday_rvol") <= 0:
            raise ValueError("SwingTrade minimum_intraday_rvol must be positive")
        behavior.boolean("require_vwap_gate")
    if implementation == "1.2.0":
        behavior.positive_int("support_freshness_sessions")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation not in {"1.0.0", "1.1.0", "1.2.0"}:
        return args, kwargs
    behavior = source.behavior()
    kwargs.update(
        fibonacci_lookback_sessions=behavior.positive_int("fibonacci_lookback_sessions"),
        movement_lookback_sessions=behavior.positive_int("movement_lookback_sessions"),
        fibonacci_50_ratio=behavior.decimal("fibonacci_50_ratio"),
        fibonacci_618_ratio=behavior.decimal("fibonacci_618_ratio"),
        fibonacci_1618_ratio=behavior.decimal("fibonacci_1618_ratio"),
        support_band_atr=behavior.decimal("support_band_atr"),
        invalidation_atr=behavior.decimal("invalidation_atr"),
        minimum_reward_risk=behavior.decimal("minimum_reward_risk"),
        maximum_distance_to_zone_atr=behavior.decimal("maximum_distance_to_zone_atr"),
        geri_freshness_sessions=behavior.positive_int("geri_freshness_sessions"),
        tracking_ttl_sessions=behavior.positive_int("tracking_ttl_sessions"),
        trade_ttl_sessions=behavior.positive_int("trade_ttl_sessions"),
        strategy_version=source.version,
    )
    if implementation in {"1.1.0", "1.2.0"}:
        kwargs.update(
            minimum_intraday_rvol=behavior.decimal("minimum_intraday_rvol"),
            minimum_rvol_samples=behavior.positive_int("minimum_rvol_samples"),
            require_vwap_gate=behavior.boolean("require_vwap_gate"),
        )
    if implementation == "1.2.0":
        kwargs["support_freshness_sessions"] = behavior.positive_int("support_freshness_sessions")
    return args, kwargs
