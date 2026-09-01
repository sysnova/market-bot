"""Assembly adapter for versioned Order Flow scope policy."""

from __future__ import annotations

from app.common.strategy import StrategySource

from .config import load_order_flow_policy


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation == "1.0.0":
        if source.artifact is not None:
            raise ValueError("Order Flow 1.0 uses its embedded wildcard policy")
        return
    if source.artifact is None:
        raise ValueError("Order Flow 1.1+ requires a bounded strategy artifact")
    load_order_flow_policy(source.artifact)


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation == "1.0.0":
        return args, kwargs
    if source.artifact is None:
        raise ValueError("Order Flow 1.1+ requires a bounded strategy artifact")
    kwargs["policy"] = load_order_flow_policy(source.artifact)
    return args, kwargs
