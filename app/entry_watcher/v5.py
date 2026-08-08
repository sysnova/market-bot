"""No-retest higher-low continuation policy preserving v4 for replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisHorizon, AnalysisResult, new_uuid7

from .engine import EntryWatcherPolicy
from .models import EntryWatch
from .ports import EntryWatchStore
from .v4 import EntryWatcherV4

FOUR_PLACES = Decimal("0.0001")


class EntryWatcherV5(EntryWatcherV4):
    """Allow an efficient higher-low continuation without a prior Long-zone touch."""

    engine_version = "5.0.0"

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
    ) -> None:
        super().__init__(
            store=store,
            policy=policy,
            id_factory=id_factory,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
        )
        self._no_retest_higher_low_enabled = no_retest_higher_low_enabled

    def _continuation_candidate(
        self,
        watch: EntryWatch,
        *,
        current_price: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal | str] | None:
        touched = self._zone_touched_at(watch)
        if touched is not None:
            return super()._continuation_candidate(
                watch,
                current_price=current_price,
                analyses=analyses,
                now=now,
            )
        if not self._no_retest_higher_low_enabled or current_price <= watch.zone_high:
            return None

        intraday = analyses.get(AnalysisHorizon.INTRADAY)
        if intraday is None:
            return None
        metrics = _metrics(intraday)
        trigger = _decimal(metrics.get("entry_trigger_level"))
        atr14 = _decimal(metrics.get("atr14"))
        if trigger is None or trigger <= Decimal("0") or atr14 is None or atr14 <= Decimal("0"):
            return None

        extension = max(Decimal("0"), current_price - trigger)
        extension_percent = (extension / trigger * Decimal("100")).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
        )
        extension_atr = (extension / atr14).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
        )
        if (
            extension_percent > self._policy.continuation_max_percent
            or extension_atr > self._policy.continuation_max_atr
        ):
            return None
        return extension_percent, extension_atr

    def _continuation_reasons(
        self,
        watch: EntryWatch,
        *,
        extension_percent: Decimal,
        extension_atr: Decimal | str,
        reward_risk: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[str, ...]:
        if self._zone_touched_at(watch) is not None:
            return super()._continuation_reasons(
                watch,
                extension_percent=extension_percent,
                extension_atr=extension_atr,
                reward_risk=reward_risk,
                analyses=analyses,
            )
        return (
            "no_retest_higher_low_continuation_confirmed",
            "consistent_five_minute_higher_low",
            "fresh_mature_intraday_reconfirmed",
            f"continuation_extension_percent:{extension_percent}",
            f"continuation_extension_atr:{extension_atr}",
            f"continuation_reward_risk:{reward_risk}",
            self._dilution_warning(analyses),
        )


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
