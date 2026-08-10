"""Assembly adapter owned by Entry Recovery business rules."""

from __future__ import annotations

from datetime import timedelta

from app.common.strategy import StrategySource

from .engine import EntryRecoveryPolicy


def validate_strategy(implementation: str, source: StrategySource) -> None:
    del implementation
    behavior = source.behavior()
    behavior.positive_int("intraday_max_age_minutes")
    behavior.positive_int("swing_max_age_days")
    behavior.decimal("minimum_reward_risk")
    behavior.boolean("strong_confirmation_required")
    behavior.boolean("five_minute_higher_low_required")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    del implementation
    behavior = source.behavior()
    kwargs["policy"] = EntryRecoveryPolicy(
        version=source.version,
        intraday_max_age=timedelta(
            minutes=behavior.positive_int("intraday_max_age_minutes")
        ),
        swing_max_age=timedelta(days=behavior.positive_int("swing_max_age_days")),
        minimum_reward_risk=behavior.decimal("minimum_reward_risk"),
        require_strong_confirmation=behavior.boolean(
            "strong_confirmation_required"
        ),
        require_five_minute_higher_low=behavior.boolean(
            "five_minute_higher_low_required"
        ),
    )
    return args, kwargs
