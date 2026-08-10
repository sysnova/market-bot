"""Assembly adapter owned by Patreon Caps business rules."""

from __future__ import annotations

from app.common.strategy import StrategySource

from .config import load_patreon_caps_policy
from .models import PatreonCapsPolicy


def validate_strategy(implementation: str, source: StrategySource) -> None:
    del implementation
    _load_policy(source)


def resolve_strategy(
    implementation: str,
    source: StrategySource,
    context: dict[str, object],
) -> PatreonCapsPolicy:
    del implementation, context
    return _load_policy(source)


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    policy = resolve_strategy(implementation, source, {})
    return (policy, *args), kwargs


def _load_policy(source: StrategySource) -> PatreonCapsPolicy:
    if source.artifact is None:
        raise ValueError("Patreon Caps strategy requires an artifact")
    policy = load_patreon_caps_policy(source.artifact)
    if policy.rule_version != source.version:
        raise ValueError(
            "Patreon Caps strategy version mismatch: "
            f"definition={source.version}, artifact={policy.rule_version}"
        )
    return policy
