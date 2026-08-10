"""Assembly adapter owned by Portfolio Flow business rules."""

from __future__ import annotations

from app.common.strategy import StrategySource

from .config import load_portfolio_flow_policy


def validate_strategy(implementation: str, source: StrategySource) -> None:
    del implementation
    if source.artifact is None:
        raise ValueError("Portfolio Flow strategy requires an artifact")
    load_portfolio_flow_policy(source.artifact)


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    del implementation
    if source.artifact is None:
        raise ValueError("Portfolio Flow strategy requires an artifact")
    kwargs["policy"] = load_portfolio_flow_policy(source.artifact)
    return args, kwargs
