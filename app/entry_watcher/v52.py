"""Quality-gated initial arming while preserving Entry Watcher v5.1 for replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, EntryWatchTransition, new_uuid7

from .engine import EntryWatcherPolicy
from .ports import EntryWatchStore
from .v51 import EntryWatcherV51

HUNDRED = Decimal("100")


class EntryWatcherV52(EntryWatcherV51):
    """Create radar watches only for nearby, non-extended Long candidates."""

    engine_version = "5.2.0"

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
    ) -> None:
        super().__init__(
            store=store,
            policy=policy,
            id_factory=id_factory,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            no_retest_higher_low_enabled=no_retest_higher_low_enabled,
            zone_exit_buffer_percent=zone_exit_buffer_percent,
        )
        for name, value in (
            ("initial_arm_min_score", initial_arm_min_score),
            ("initial_arm_max_distance_percent", initial_arm_max_distance_percent),
            ("initial_arm_max_distance_atr", initial_arm_max_distance_atr),
        ):
            if not value.is_finite() or value <= Decimal("0"):
                raise ValueError(f"{name} must be finite and positive")
        self._initial_arm_min_score = initial_arm_min_score
        self._initial_arm_max_distance_percent = initial_arm_max_distance_percent
        self._initial_arm_max_distance_atr = initial_arm_max_distance_atr

    async def _arm(
        self,
        result: AnalysisResult,
        *,
        now: datetime,
    ) -> EntryWatchTransition | None:
        metrics = {item.name: item.value for item in result.metrics}
        if metrics.get("classification") == "extended":
            return None
        if result.score < self._initial_arm_min_score:
            return None
        price = _decimal(metrics.get("reference_price"))
        zone_high = _decimal(metrics.get("buy_zone_high"))
        if price is None or zone_high is None:
            return None
        if price > zone_high:
            distance_percent = (price - zone_high) / price * HUNDRED
            distance_atr = _decimal(metrics.get("distance_to_buy_zone_atr"))
            if (
                distance_percent > self._initial_arm_max_distance_percent
                or distance_atr is None
                or distance_atr > self._initial_arm_max_distance_atr
            ):
                return None
        return await super()._arm(result, now=now)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str | int):
        try:
            return Decimal(value)
        except ArithmeticError:
            return None
    return None
