"""Fresh event-price anchoring for Entry Watcher transitions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, EntryWatchTransition, new_uuid7

from .engine import EntryWatcherPolicy
from .ports import EntryWatchStore
from .v55 import EntryWatcherV55


class EntryWatcherV56(EntryWatcherV55):
    """Require a contemporary market observation before changing watch state.

    Analysis events are replayed when the service starts.  They remain useful as
    structural context, but their old ``reference_price`` must not become the
    price of a newly emitted ARMED, entry, or confirmation transition.
    """

    engine_version = "5.6.0"

    def __init__(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
        id_factory: Callable[[], UUID] = new_uuid7,
        minimum_reconfirmation_delay: timedelta = timedelta(minutes=3),
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        no_retest_higher_low_enabled: bool = True,
        zone_exit_buffer_percent: Decimal = Decimal("0.25"),
        initial_arm_min_score: Decimal = Decimal("50"),
        initial_arm_max_distance_percent: Decimal = Decimal("4"),
        initial_arm_max_distance_atr: Decimal = Decimal("2"),
        trigger_on_first_mature_confirmation: bool = True,
        early_entry_max_extension_percent: Decimal = Decimal("4"),
        early_entry_max_extension_atr: Decimal = Decimal("1"),
        early_entry_min_reward_risk: Decimal = Decimal("1.5"),
        pullback_min_retracement: Decimal = Decimal("0.382"),
        pullback_max_retracement: Decimal = Decimal("0.618"),
        pullback_stop_atr_buffer: Decimal = Decimal("0.25"),
        pullback_min_reward_risk: Decimal = Decimal("2"),
        transition_price_max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        if transition_price_max_age <= timedelta(0):
            raise ValueError("transition price maximum age must be positive")
        super().__init__(
            store=store,
            policy=policy,
            id_factory=id_factory,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            no_retest_higher_low_enabled=no_retest_higher_low_enabled,
            zone_exit_buffer_percent=zone_exit_buffer_percent,
            initial_arm_min_score=initial_arm_min_score,
            initial_arm_max_distance_percent=initial_arm_max_distance_percent,
            initial_arm_max_distance_atr=initial_arm_max_distance_atr,
            trigger_on_first_mature_confirmation=trigger_on_first_mature_confirmation,
            early_entry_max_extension_percent=early_entry_max_extension_percent,
            early_entry_max_extension_atr=early_entry_max_extension_atr,
            early_entry_min_reward_risk=early_entry_min_reward_risk,
            pullback_min_retracement=pullback_min_retracement,
            pullback_max_retracement=pullback_max_retracement,
            pullback_stop_atr_buffer=pullback_stop_atr_buffer,
            pullback_min_reward_risk=pullback_min_reward_risk,
        )
        self._transition_price_max_age = transition_price_max_age

    async def ingest(
        self,
        result: AnalysisResult,
        *,
        now: datetime,
    ) -> EntryWatchTransition | None:
        self._validate_time(result, now)
        latest = self._latest.setdefault(result.symbol, {})
        existing = latest.get(result.horizon)
        if existing is not None and result.as_of < existing.as_of:
            return None
        latest[result.horizon] = result

        observation = self._current_price_observation(latest)
        if observation is None or now - observation[0] > self._transition_price_max_age:
            # Hydrate replayed analysis context without deriving a transition
            # whose timestamp and price refer to different market moments.
            return None
        return await super().ingest(result, now=now)
