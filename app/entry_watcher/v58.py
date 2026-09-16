# pyright: reportPrivateUsage=false
"""Keep the stop, objective and risk/reward attached to the triggering rule."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from app.contracts import AnalysisHorizon, AnalysisResult, EntryWatchStatus, EntryWatchTransition

from .engine import JsonValue
from .models import EntryWatch
from .v54 import TWO_PLACES, _decimal, _early_confirmation, _metrics, _rounded
from .v57 import EntryWatcherV57, _swing_approved


class EntryWatcherV58(EntryWatcherV57):
    engine_version = "5.8.0"

    def _early_entry_levels(
        self,
        watch: EntryWatch,
        *,
        price: Decimal,
        analyses: Mapping[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        if (
            watch.status is EntryWatchStatus.IMPULSE_EXTENDED
            or not _swing_approved(analyses)
            or not self._fresh_core_analyses(analyses, now=now)
            or not _early_confirmation(analyses)
        ):
            return None
        levels = _intraday_levels(analyses, price)
        if levels is None or levels[2] < self._early_min_rr:
            return None
        stop, target, rr = levels
        return stop, target, _rounded(rr, TWO_PLACES)

    def _continuation_reward_risk(
        self,
        watch: EntryWatch,
        *,
        current_price: Decimal,
        analyses: Mapping[AnalysisHorizon, AnalysisResult],
    ) -> Decimal | None:
        levels = _intraday_levels(analyses, current_price)
        # The caller applies its continuation threshold. Do not round up a failure.
        return levels[2] if levels is not None else None

    def _confirmed(
        self, analyses: Mapping[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        price = self._current_price(analyses)
        return bool(
            price is not None
            and _intraday_levels(analyses, price) is not None
            and super()._confirmed(analyses, now=now)
        )

    async def _change(
        self,
        watch: EntryWatch,
        status: EntryWatchStatus,
        *,
        now: datetime,
        price: Decimal,
        reasons: tuple[str, ...],
        analyses: Mapping[AnalysisHorizon, AnalysisResult],
        anchor_updates: dict[str, JsonValue] | None = None,
        entry_invalidation: Decimal | None = None,
        entry_target: Decimal | None = None,
    ) -> EntryWatchTransition:
        if status in {EntryWatchStatus.EARLY_ENTRY, EntryWatchStatus.TRIGGERED}:
            if "dynamic_pullback_entry_confirmed" in reasons:
                # This rule owns both the observed peak and its buffered pullback low.
                source = "OBSERVED_IMPULSE_PULLBACK"
                evidence: dict[str, JsonValue] = {"source": source}
            else:
                levels = _intraday_levels(analyses, price)
                if levels is None:
                    raise ValueError("confirmed intraday entry requires its own valid level pair")
                entry_invalidation, entry_target, _ = levels
                source = "INTRADAY"
                analysis = analyses[AnalysisHorizon.INTRADAY]
                metrics = _metrics(analysis)
                evidence = {
                    "source": source,
                    "analysis_id": str(analysis.analysis_id),
                    "engine_version": analysis.engine_version,
                    "as_of": analysis.as_of.isoformat(),
                    "setup": str(metrics.get("setup", "unknown")),
                    "rule_version": str(metrics.get("entry_confirmation_rule_version", "unknown")),
                }
            if entry_invalidation is None or entry_target is None:
                raise ValueError("confirmed entry requires its rule's stop and objective")
            evidence.update(
                entry_price=str(price),
                invalidation=str(entry_invalidation),
                target=str(entry_target),
                reward_risk=str((entry_target - price) / (price - entry_invalidation)),
            )
            anchor_updates = {**(anchor_updates or {}), "entry_rule_levels": evidence}
            reasons += (f"entry_levels_source:{source}",)
        return await super()._change(
            watch,
            status,
            now=now,
            price=price,
            reasons=reasons,
            analyses=analyses,
            anchor_updates=anchor_updates,
            entry_invalidation=entry_invalidation,
            entry_target=entry_target,
        )


def _intraday_levels(
    analyses: Mapping[AnalysisHorizon, AnalysisResult],
    price: Decimal,
) -> tuple[Decimal, Decimal, Decimal] | None:
    intraday = analyses.get(AnalysisHorizon.INTRADAY)
    if intraday is None or not price.is_finite() or price <= 0:
        return None
    metrics = _metrics(intraday)
    stop = _decimal(metrics.get("invalidation_level"))
    target = _decimal(metrics.get("objective_level"))
    if (
        stop is None
        or target is None
        or not stop.is_finite()
        or not target.is_finite()
        or not 0 < stop < price < target
    ):
        return None
    return stop, target, (target - price) / (price - stop)
