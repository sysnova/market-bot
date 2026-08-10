"""Assembly adapter owned by Entry Watcher business rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import cast

from app.common.strategy import StrategySource

from .engine import EntryWatcherPolicy

_CONFIGURED_IMPLEMENTATIONS = {"4.0.0", "5.0.0", "5.1.0"}


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in _CONFIGURED_IMPLEMENTATIONS:
        return
    behavior = source.behavior()
    behavior.positive_int("fresh_reconfirmation_delay_minutes")
    behavior.positive_int("trigger_rearm_cooldown_minutes")
    behavior.boolean("strong_confirmation_required")
    behavior.boolean("five_minute_higher_low_required")
    if implementation in {"5.0.0", "5.1.0"}:
        behavior.boolean("no_retest_higher_low_continuation")
    if implementation == "5.1.0":
        behavior.decimal("zone_exit_buffer_percent")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    if implementation not in _CONFIGURED_IMPLEMENTATIONS:
        return args, kwargs
    behavior = source.behavior()
    policy_value = kwargs.get("policy")
    policy = (
        EntryWatcherPolicy()
        if policy_value is None
        else cast("EntryWatcherPolicy", policy_value)
    )
    kwargs.update(
        policy=replace(
            policy,
            trigger_rearm_cooldown=timedelta(
                minutes=behavior.positive_int("trigger_rearm_cooldown_minutes")
            ),
        ),
        minimum_reconfirmation_delay=timedelta(
            minutes=behavior.positive_int("fresh_reconfirmation_delay_minutes")
        ),
        strong_confirmation_required=behavior.boolean(
            "strong_confirmation_required"
        ),
        five_minute_higher_low_required=behavior.boolean(
            "five_minute_higher_low_required"
        ),
    )
    if implementation in {"5.0.0", "5.1.0"}:
        kwargs["no_retest_higher_low_enabled"] = behavior.boolean(
            "no_retest_higher_low_continuation"
        )
    if implementation == "5.1.0":
        kwargs["zone_exit_buffer_percent"] = behavior.decimal(
            "zone_exit_buffer_percent"
        )
    return args, kwargs
