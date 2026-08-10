"""Assembly adapter owned by LONG Portfolio business rules."""

from __future__ import annotations

from typing import cast

from app.common.strategy import StrategySource

from .config import load_long_portfolio_policy
from .models import LongPortfolioPolicy, PortfolioAllocation


def validate_strategy(implementation: str, source: StrategySource) -> None:
    del implementation
    _load_policy(source, ())


def resolve_strategy(
    implementation: str,
    source: StrategySource,
    context: dict[str, object],
) -> LongPortfolioPolicy:
    del implementation
    allocations = _allocations(context.get("allocations", ()))
    return _load_policy(source, allocations)


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    policy = resolve_strategy(
        implementation,
        source,
        {"allocations": kwargs.pop("allocations", ())},
    )
    return (policy, *args), kwargs


def _load_policy(
    source: StrategySource,
    allocations: tuple[PortfolioAllocation, ...],
) -> LongPortfolioPolicy:
    if source.artifact is None:
        raise ValueError("LONG Portfolio strategy requires an artifact")
    policy = load_long_portfolio_policy(source.artifact, allocations=allocations)
    if policy.rule_version != source.version:
        raise ValueError(
            "LONG Portfolio strategy version mismatch: "
            f"definition={source.version}, artifact={policy.rule_version}"
        )
    return policy


def _allocations(value: object) -> tuple[PortfolioAllocation, ...]:
    if not isinstance(value, tuple):
        raise ValueError("LONG Portfolio allocations must be PortfolioAllocation values")
    items = cast("tuple[object, ...]", value)
    if not all(isinstance(item, PortfolioAllocation) for item in items):
        raise ValueError("LONG Portfolio allocations must be PortfolioAllocation values")
    return cast("tuple[PortfolioAllocation, ...]", items)
