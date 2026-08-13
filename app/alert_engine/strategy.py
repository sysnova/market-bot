"""Assembly adapter owned by Alert business rules."""

from __future__ import annotations

from datetime import timedelta

from app.common.strategy import StrategySource
from app.contracts import AnalysisHorizon, EntryMaturityLevel

_CONFIGURED_IMPLEMENTATIONS = {
    "3.0.0",
    "3.1.0",
    "3.2.0",
    "3.3.0",
    "3.4.0",
    "3.5.0",
}


def validate_strategy(implementation: str, source: StrategySource) -> None:
    if implementation not in _CONFIGURED_IMPLEMENTATIONS:
        return
    behavior = source.behavior()
    behavior.positive_int("fresh_reconfirmation_delay_minutes")
    behavior.boolean("strong_confirmation_required")
    behavior.boolean("five_minute_higher_low_required")
    behavior.boolean("same_market_session_required")
    if implementation in {"3.2.0", "3.3.0", "3.4.0", "3.5.0"}:
        tuple(
            AnalysisHorizon(value)
            for value in behavior.non_empty_unique_strings(
                "recovery_required_horizons"
            )
        )
        EntryMaturityLevel(str(behavior.values["recovery_maturity"]))
    if implementation in {"3.3.0", "3.4.0", "3.5.0"}:
        minimum = behavior.decimal("minimum_swing_reward_risk_to_resistance")
        if minimum <= 0:
            raise ValueError(
                "strategy behavior minimum_swing_reward_risk_to_resistance must be positive"
            )
        behavior.boolean("intraday_mature_gate_required")


def configure_engine(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    restored_state = kwargs.pop("restored_state", None)
    if implementation not in _CONFIGURED_IMPLEMENTATIONS:
        if restored_state is not None:
            raise ValueError(
                "restored Alert state requires alert implementation 3.1.0 or newer"
            )
        return args, kwargs
    behavior = source.behavior()
    kwargs.update(
        minimum_reconfirmation_delay=timedelta(
            minutes=behavior.positive_int("fresh_reconfirmation_delay_minutes")
        ),
        strong_confirmation_required=behavior.boolean(
            "strong_confirmation_required"
        ),
        five_minute_higher_low_required=behavior.boolean(
            "five_minute_higher_low_required"
        ),
        same_market_session_required=behavior.boolean(
            "same_market_session_required"
        ),
    )
    if implementation in {"3.1.0", "3.2.0", "3.3.0", "3.4.0", "3.5.0"}:
        kwargs["restored_state"] = restored_state
    elif restored_state is not None:
        raise ValueError(
            "restored Alert state requires alert implementation 3.1.0 or newer"
        )
    if implementation in {"3.2.0", "3.3.0", "3.4.0", "3.5.0"}:
        kwargs.update(
            recovery_required_horizons=tuple(
                AnalysisHorizon(value)
                for value in behavior.non_empty_unique_strings(
                    "recovery_required_horizons"
                )
            ),
            recovery_maturity=EntryMaturityLevel(
                str(behavior.values["recovery_maturity"])
            ),
        )
    if implementation in {"3.3.0", "3.4.0", "3.5.0"}:
        kwargs.update(
            minimum_swing_reward_risk_to_resistance=behavior.decimal(
                "minimum_swing_reward_risk_to_resistance"
            ),
            intraday_mature_gate_required=behavior.boolean(
                "intraday_mature_gate_required"
            ),
        )
    return args, kwargs
