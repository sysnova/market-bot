"""Price-efficient Entry Watcher v4 policy preserving v3 for replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    EntryWatchTransition,
    PatternDirection,
    new_uuid7,
)

from .engine import EntryWatcherPolicy
from .ports import EntryWatchStore
from .v2 import EntryWatcherV2

_MIN_RECONFIRMATION_DELAY = timedelta(minutes=3)


class EntryWatcherV4(EntryWatcherV2):
    """Require an efficient retest and a fresh second Intraday confirmation."""

    engine_version = "4.0.0"

    def __init__(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
        id_factory: Callable[[], UUID] = new_uuid7,
        minimum_reconfirmation_delay: timedelta = _MIN_RECONFIRMATION_DELAY,
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
    ) -> None:
        super().__init__(store=store, policy=policy, id_factory=id_factory)
        if minimum_reconfirmation_delay <= timedelta():
            raise ValueError("minimum reconfirmation delay must be positive")
        self._minimum_reconfirmation_delay = minimum_reconfirmation_delay
        self._strong_confirmation_required = strong_confirmation_required
        self._five_minute_higher_low_required = five_minute_higher_low_required
        self._mature_candidates: dict[str, tuple[UUID, datetime]] = {}
        self._reconfirmed_ids: set[UUID] = set()

    async def ingest(self, result: AnalysisResult, *, now: datetime) -> EntryWatchTransition | None:
        if result.horizon is AnalysisHorizon.LONG_TERM:
            active = await self._store.load_active(result.symbol)
            if active is None:
                previous = await self._store.load_latest(result.symbol)
                if (
                    previous is not None
                    and previous.status is EntryWatchStatus.TRIGGERED
                    and now - previous.updated_at < self._policy.trigger_rearm_cooldown
                ):
                    return None
        if result.horizon is AnalysisHorizon.INTRADAY:
            self._observe_mature_candidate(result)
        transition = await super().ingest(result, now=now)
        if transition is not None and transition.status is EntryWatchStatus.TRIGGERED:
            self._mature_candidates.pop(result.symbol, None)
            self._reconfirmed_ids.difference_update(transition.source_analysis_ids)
        return transition

    def _observe_mature_candidate(self, result: AnalysisResult) -> None:
        if not self._intraday_mature(result):
            return
        candidate = self._mature_candidates.get(result.symbol)
        if candidate is None:
            self._mature_candidates[result.symbol] = (result.analysis_id, result.as_of)
            return
        candidate_id, candidate_at = candidate
        if result.analysis_id == candidate_id:
            return
        if result.as_of - candidate_at >= self._minimum_reconfirmation_delay:
            self._reconfirmed_ids.add(result.analysis_id)

    def _confirmed(self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime) -> bool:
        if not EntryWatcherV2._confirmed(self, analyses, now=now):
            return False
        return self._v4_gates_pass(analyses)

    def _continuation_confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        if EntryWatcherV2._confirmed(self, analyses, now=now):
            return self._v4_gates_pass(analyses)
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        if not required.issubset(analyses):
            return False
        if any(
            now - analyses[horizon].as_of > self._policy.max_ages[horizon] for horizon in required
        ):
            return False
        long_term = analyses[AnalysisHorizon.LONG_TERM]
        swing = analyses[AnalysisHorizon.SWING]
        swing_metrics = _metrics(swing)
        return (
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.CAUTION}
            and swing_metrics.get("classification") in {"breakout", "pullback", "extended"}
            and self._v4_gates_pass(analyses)
        )

    def _v4_gates_pass(self, analyses: dict[AnalysisHorizon, AnalysisResult]) -> bool:
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        intraday_metrics = _metrics(intraday)
        return (
            _metrics(swing).get("anchored_vwap_gate_passed") is True
            and self._intraday_mature(intraday)
            and intraday.analysis_id in self._reconfirmed_ids
            and intraday_metrics.get("confirmation_gate_passed") is True
        )

    def _intraday_mature(self, result: AnalysisResult) -> bool:
        metrics = _metrics(result)
        return (
            result.direction is PatternDirection.BULLISH
            and result.verdict is AnalysisVerdict.FAVORABLE
            and metrics.get("confirmation_gate_passed") is True
            and metrics.get("mature_confirmation_gate_passed") is True
            and metrics.get("entry_efficiency_gate_passed") is True
            and (
                not self._strong_confirmation_required
                or metrics.get("confirmation_quality") == "strong"
            )
            and (
                not self._five_minute_higher_low_required
                or metrics.get("five_minute_higher_low") is True
            )
        )

    def _confirmation_reasons(
        self, analyses: dict[AnalysisHorizon, AnalysisResult]
    ) -> tuple[str, ...]:
        return (
            "price_efficient_entry_confirmed",
            "fresh_mature_intraday_reconfirmed",
            self._dilution_warning(analyses),
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
