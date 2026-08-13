"""Immediate mature confirmation while preserving v5.2 radar behavior."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisHorizon, AnalysisResult, new_uuid7

from .engine import EntryWatcherPolicy
from .models import EntryWatch
from .ports import EntryWatchStore
from .v52 import EntryWatcherV52


class EntryWatcherV53(EntryWatcherV52):
    """Trigger L4 on the first fully mature, price-efficient confirmation."""

    engine_version = "5.3.0"

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
            initial_arm_min_score=initial_arm_min_score,
            initial_arm_max_distance_percent=initial_arm_max_distance_percent,
            initial_arm_max_distance_atr=initial_arm_max_distance_atr,
        )
        self._trigger_on_first_mature_confirmation = (
            trigger_on_first_mature_confirmation
        )

    def _v4_gates_pass(self, analyses: dict[AnalysisHorizon, AnalysisResult]) -> bool:
        if not self._trigger_on_first_mature_confirmation:
            return super()._v4_gates_pass(analyses)
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        intraday_metrics = _metrics(intraday)
        return bool(
            _metrics(swing).get("anchored_vwap_gate_passed") is True
            and self._intraday_mature(intraday)
            and intraday_metrics.get("confirmation_gate_passed") is True
        )

    def _confirmation_reasons(
        self,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[str, ...]:
        if not self._trigger_on_first_mature_confirmation:
            return super()._confirmation_reasons(analyses)
        return (
            "price_efficient_entry_confirmed",
            "mature_intraday_entry_confirmed",
            self._dilution_warning(analyses),
        )

    def _continuation_reasons(
        self,
        watch: EntryWatch,
        *,
        extension_percent: Decimal,
        extension_atr: Decimal | str,
        reward_risk: Decimal,
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[str, ...]:
        reasons = super()._continuation_reasons(
            watch,
            extension_percent=extension_percent,
            extension_atr=extension_atr,
            reward_risk=reward_risk,
            analyses=analyses,
        )
        if not self._trigger_on_first_mature_confirmation:
            return reasons
        return tuple(
            "mature_intraday_entry_confirmed"
            if reason == "fresh_mature_intraday_reconfirmed"
            else reason
            for reason in reasons
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
