"""Assembly adapter owned by Entry Watcher business rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import cast

from app.common.strategy import StrategySource

from .engine import EntryWatcherPolicy

_CONFIGURED_IMPLEMENTATIONS = {"4.0.0", "5.0.0", "5.1.0", "5.2.0", "5.3.0", "5.4.0"}


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in _CONFIGURED_IMPLEMENTATIONS:
        return
    behavior = source.behavior()
    behavior.positive_int("fresh_reconfirmation_delay_minutes")
    behavior.positive_int("trigger_rearm_cooldown_minutes")
    behavior.boolean("strong_confirmation_required")
    behavior.boolean("five_minute_higher_low_required")
    if implementation in {"5.0.0", "5.1.0", "5.2.0", "5.3.0", "5.4.0"}:
        behavior.boolean("no_retest_higher_low_continuation")
    if implementation in {"5.1.0", "5.2.0", "5.3.0", "5.4.0"}:
        behavior.decimal("zone_exit_buffer_percent")
    if implementation in {"5.2.0", "5.3.0", "5.4.0"}:
        behavior.decimal("initial_arm_min_score")
        behavior.decimal("initial_arm_max_distance_percent")
        behavior.decimal("initial_arm_max_distance_atr")
    if implementation in {"5.3.0", "5.4.0"}:
        behavior.boolean("trigger_on_first_mature_confirmation")
    if implementation == "5.4.0":
        for name in (
            "early_entry_max_extension_percent",
            "early_entry_max_extension_atr",
            "early_entry_min_reward_risk",
            "pullback_min_retracement",
            "pullback_max_retracement",
            "pullback_stop_atr_buffer",
            "pullback_min_reward_risk",
        ):
            behavior.decimal(name)


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
    if implementation in {"5.0.0", "5.1.0", "5.2.0", "5.3.0", "5.4.0"}:
        kwargs["no_retest_higher_low_enabled"] = behavior.boolean(
            "no_retest_higher_low_continuation"
        )
    if implementation in {"5.1.0", "5.2.0", "5.3.0", "5.4.0"}:
        kwargs["zone_exit_buffer_percent"] = behavior.decimal(
            "zone_exit_buffer_percent"
        )
    if implementation in {"5.2.0", "5.3.0", "5.4.0"}:
        kwargs.update(
            initial_arm_min_score=behavior.decimal("initial_arm_min_score"),
            initial_arm_max_distance_percent=behavior.decimal(
                "initial_arm_max_distance_percent"
            ),
            initial_arm_max_distance_atr=behavior.decimal(
                "initial_arm_max_distance_atr"
            ),
        )
    if implementation in {"5.3.0", "5.4.0"}:
        kwargs["trigger_on_first_mature_confirmation"] = behavior.boolean(
            "trigger_on_first_mature_confirmation"
        )
    if implementation == "5.4.0":
        kwargs.update(
            early_entry_max_extension_percent=behavior.decimal(
                "early_entry_max_extension_percent"
            ),
            early_entry_max_extension_atr=behavior.decimal(
                "early_entry_max_extension_atr"
            ),
            early_entry_min_reward_risk=behavior.decimal(
                "early_entry_min_reward_risk"
            ),
            pullback_min_retracement=behavior.decimal("pullback_min_retracement"),
            pullback_max_retracement=behavior.decimal("pullback_max_retracement"),
            pullback_stop_atr_buffer=behavior.decimal("pullback_stop_atr_buffer"),
            pullback_min_reward_risk=behavior.decimal("pullback_min_reward_risk"),
        )
    return args, kwargs
