# pyright: reportPrivateUsage=false
"""Give Core Recovery its Swing lifecycle and require recent analytical exit marks."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntrySignalFamily,
    MarketBar,
    PatternDirection,
    TradeSide,
)

from .engine import _CORE_ENGINES, _checkpoint_horizons, _leg_family, _metric_decimal
from .v15 import EntryOpportunityEngineV15

_ENTRY = frozenset({EntrySignalFamily.CORE_ENTRY})
_RECOVERY = frozenset({EntrySignalFamily.CORE_RECOVERY})
_EXIT_MARK_MAX_AGE = timedelta(minutes=5)


class EntryOpportunityEngineV16(EntryOpportunityEngineV15):
    engine_version = "16.0.0"

    async def ingest_analysis(
        self, result: AnalysisResult, *, now: datetime
    ) -> tuple[EntryOpportunityEvent, ...]:
        if result.as_of > now:
            return ()
        return await super().ingest_analysis(result, now=now)

    @staticmethod
    def _fresh_exit_mark(
        active: EntryOpportunity, result: AnalysisResult, *, now: datetime
    ) -> tuple[datetime, Decimal] | None:
        observations = [(active.armed_at, active.original_price)]
        observations.extend((cp.reached_at, cp.entry_price) for cp in active.checkpoints)
        if active.last_market_bar_at is not None:
            observations.append((active.last_market_bar_at, active.current_price))
        for analysis in (*active.latest_analyses, result):
            price = _metric_decimal(analysis, "reference_price")
            if price is not None:
                observations.append((analysis.as_of, price))
        eligible = [
            (at, price)
            for at, price in observations
            if price.is_finite() and price > 0 and timedelta(0) <= now - at <= _EXIT_MARK_MAX_AGE
        ]
        return max(eligible, key=lambda item: item[0]) if eligible else None

    def _apply_analysis_invalidation(
        self,
        active: EntryOpportunity,
        updated: EntryOpportunity,
        *,
        result: AnalysisResult,
        now: datetime,
    ) -> tuple[EntryOpportunity, tuple[str, ...]] | None:
        if result.engine_id != _CORE_ENGINES.get(result.horizon) or result.as_of > now:
            return None
        bearish = result.direction is PatternDirection.BEARISH and result.verdict in {
            AnalysisVerdict.AVOID,
            AnalysisVerdict.CAUTION,
        }
        long_failure = result.horizon is AnalysisHorizon.LONG_TERM and (
            result.verdict is AnalysisVerdict.AVOID or bearish
        )
        mark = self._fresh_exit_mark(active, result, now=now)
        changed = updated
        reasons: list[str] = []
        if mark is not None:
            price_at, price = mark
            # A contradictory Long opinion never disables either family's own price stop.
            changed = self._close_core_entries(
                changed,
                price=price,
                now=now,
                evidence_at=price_at,
                reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
                leg_status=EntryLegStatus.INVALIDATED,
                price_breach_only=True,
            )
            if changed != updated:
                reasons.append("core_invalidation_breached")
            if long_failure or bearish:
                before = changed
                changed = self._close_core_entries(
                    changed,
                    price=price,
                    now=now,
                    evidence_at=result.as_of,
                    reason=(
                        EntryCloseReason.ORIGINAL_THESIS_INVALIDATED
                        if long_failure
                        else EntryCloseReason.ALL_HORIZONS_CLOSED
                    ),
                    leg_status=(
                        EntryLegStatus.THESIS_BROKEN if long_failure else EntryLegStatus.INVALIDATED
                    ),
                    horizon=None if long_failure else result.horizon,
                    families=_ENTRY,
                )
                if bearish and result.horizon is AnalysisHorizon.SWING:
                    changed = self._close_core_entries(
                        changed,
                        price=price,
                        now=now,
                        evidence_at=result.as_of,
                        reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
                        leg_status=EntryLegStatus.INVALIDATED,
                        families=_RECOVERY,
                    )
                if changed != before:
                    reasons.append(
                        "long_structure_invalidated"
                        if long_failure
                        else f"{result.horizon.value.lower()}_invalidated"
                    )
        # Stored analysis is also the durable pending evidence. Revisit it on a fresh bar.
        if active is not updated:
            previous = next(
                (a for a in active.latest_analyses if a.horizon is result.horizon), None
            )
            previous_failure = (
                previous is not None
                and previous.engine_id == result.engine_id
                and (
                    (
                        previous.direction is PatternDirection.BEARISH
                        and previous.verdict in {AnalysisVerdict.CAUTION, AnalysisVerdict.AVOID}
                    )
                    or (
                        previous.horizon is AnalysisHorizon.LONG_TERM
                        and previous.verdict is AnalysisVerdict.AVOID
                    )
                )
            )
            if (
                long_failure
                and not previous_failure
                and any(
                    cp.signal_family is EntrySignalFamily.CORE_RECOVERY
                    and cp.status is EntryCheckpointStatus.OPEN
                    for cp in changed.checkpoints
                )
            ):
                reasons.append("core_recovery_long_bearish_context")
            pending = any(
                cp.status is EntryCheckpointStatus.OPEN
                and result.as_of >= cp.reached_at
                and (
                    (
                        cp.signal_family in _ENTRY
                        and (long_failure or result.horizon in _checkpoint_horizons(changed, cp))
                    )
                    or (cp.signal_family in _RECOVERY and result.horizon is AnalysisHorizon.SWING)
                )
                for cp in changed.checkpoints
            )
            if mark is None and (long_failure or bearish) and pending and not previous_failure:
                reasons.append("analysis_exit_waiting_fresh_price")
        return (changed, tuple(reasons)) if reasons else None

    def _close_watcher_thesis(
        self,
        opportunity: EntryOpportunity,
        *,
        price: Decimal,
        now: datetime,
        reason: EntryCloseReason,
        leg_status: EntryLegStatus,
    ) -> EntryOpportunity:
        return self._close_core_entries(
            opportunity,
            price=price,
            now=now,
            evidence_at=now,
            reason=reason,
            leg_status=leg_status,
            families=_ENTRY,
        )

    @staticmethod
    def _bar_can_close_opportunity(
        opportunity: EntryOpportunity,
        *,
        legs: tuple[EntryHorizonLeg, ...],
        checkpoints: tuple[EntryMaturityCheckpoint, ...],
    ) -> bool:
        return not any(
            cp.status is EntryCheckpointStatus.OPEN and cp.signal_family not in _ENTRY
            for cp in checkpoints
        ) and not any(
            leg.status in {EntryLegStatus.OPEN, EntryLegStatus.WATCHING}
            and _leg_family(opportunity, leg) not in _ENTRY
            for leg in legs
        )

    async def ingest_bar(self, bar: MarketBar) -> tuple[EntryOpportunityEvent, ...]:
        previous = await self._store.load_active(bar.symbol)
        events = await super().ingest_bar(bar)
        active = await self._store.load_active(bar.symbol)
        if (
            active is None
            or previous is None
            or active.trade_side is TradeSide.SHORT
            or active.last_market_bar_at != bar.timestamp
            or previous.last_market_bar_at == active.last_market_bar_at
            or bar.timestamp < previous.updated_at
        ):
            return events
        # Price stops and targets above have priority over a deferred analytical exit.
        for result in active.latest_analyses:
            invalidated = self._apply_analysis_invalidation(
                active, active, result=result, now=bar.timestamp
            )
            if invalidated is not None:
                active, reasons = invalidated
                event = self._event(active, occurred_at=bar.timestamp, reasons=reasons)
                await self._store.save(active, event)
                events = (*events, event)
        return events
