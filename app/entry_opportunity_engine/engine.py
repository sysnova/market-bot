"""Consolidate Entry Watcher evidence into one auditable paper opportunity per ticker."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.common.market_session import is_regular_session
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunitySignalReference,
    EntryOpportunitySourceCursor,
    EntryOpportunityStatus,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    GeriCountertrendMaturity,
    LeveragedThesisAssessment,
    LeveragedThesisState,
    LocalAlert,
    MarketBar,
    PatternDirection,
    SwingTradeMaturity,
    new_uuid7,
)

from .ports import EntryOpportunityStore

_NEW_YORK = ZoneInfo("America/New_York")
_FOUR_PLACES = Decimal("0.0001")
_PROGRESS = {
    EntryMaturityLevel.ARMED: Decimal("20"),
    EntryMaturityLevel.IN_ZONE: Decimal("40"),
    EntryMaturityLevel.L1: Decimal("60"),
    EntryMaturityLevel.L2: Decimal("75"),
    EntryMaturityLevel.L3: Decimal("90"),
    EntryMaturityLevel.L4: Decimal("100"),
}
_TERMINAL_LEGS = {
    EntryLegStatus.TARGET_HIT,
    EntryLegStatus.INVALIDATED,
    EntryLegStatus.SESSION_CLOSED,
    EntryLegStatus.THESIS_BROKEN,
    EntryLegStatus.EXPIRED,
    EntryLegStatus.TIME_EXIT,
}
_MAX_SOURCE_ANALYSIS_IDS = 32
_WATCHER_SOURCE = "ENTRY_WATCHER"
_LEVERAGED_CANCELLATION_SOURCE = "LEVERAGED_THESIS_CANCELLATION"
_L2_RETEST_ATR_TOLERANCE = Decimal("0.5")


class EntryOpportunityEngine:
    """Own progression, paper legs, markouts, and closure of ticker opportunities."""

    engine_id = "entry-opportunity"
    engine_version = "1.0.0"
    regress_tracking_maturity = False

    def __init__(
        self,
        *,
        store: EntryOpportunityStore,
        id_factory: Callable[[], UUID] = new_uuid7,
        child_id_factory: Callable[[], UUID] = new_uuid7,
    ) -> None:
        self._store = store
        self._id_factory = id_factory
        self._child_id_factory = child_id_factory

    async def ingest_transition(
        self, transition: EntryWatchTransition
    ) -> tuple[EntryOpportunityEvent, ...]:
        """Merge a watcher transition without creating another ticker opportunity."""

        if await self._store.event_seen(transition.transition_id):
            return ()
        active = await self._store.load_active(transition.symbol)
        if active is None:
            if transition.status not in {
                EntryWatchStatus.ARMED,
                EntryWatchStatus.IN_ZONE,
                EntryWatchStatus.EARLY_ENTRY,
                EntryWatchStatus.TRIGGERED,
            }:
                return ()
            level = {
                EntryWatchStatus.ARMED: EntryMaturityLevel.ARMED,
                EntryWatchStatus.IN_ZONE: EntryMaturityLevel.IN_ZONE,
                EntryWatchStatus.EARLY_ENTRY: EntryMaturityLevel.L1,
                EntryWatchStatus.TRIGGERED: EntryMaturityLevel.L4,
            }[transition.status]
            opportunity = self._new_opportunity(transition, level=level)
            recovered = transition.status in {
                EntryWatchStatus.EARLY_ENTRY,
                EntryWatchStatus.TRIGGERED,
            }
            if recovered:
                opportunity = self._advance(
                    opportunity,
                    level=level,
                    price=transition.current_price,
                    now=transition.occurred_at,
                    horizons=transition.horizons,
                    source_analysis_ids=transition.source_analysis_ids,
                    checkpoint_target=transition.entry_target,
                    checkpoint_invalidation=transition.entry_invalidation,
                    horizon_invalidations=(
                        {horizon: transition.entry_invalidation for horizon in transition.horizons}
                        if transition.entry_invalidation is not None
                        else None
                    ),
                    horizon_targets=(
                        {horizon: transition.entry_target for horizon in transition.horizons}
                        if transition.entry_target is not None
                        else None
                    ),
                )
            event = self._event(
                opportunity,
                occurred_at=transition.occurred_at,
                reasons=(
                    (
                        (
                            "opportunity_opened_from_early_entry"
                            if transition.status is EntryWatchStatus.EARLY_ENTRY
                            else "opportunity_recovered_from_triggered"
                        )
                        if recovered
                        else "opportunity_created"
                    ),
                    *transition.reasons,
                ),
                event_id=transition.transition_id,
            )
            await self._store.save(opportunity, event)
            return (event,)

        if not _is_new_source_event(
            active,
            source=_WATCHER_SOURCE,
            occurred_at=transition.occurred_at,
            event_id=transition.transition_id,
        ):
            return ()
        if (
            active.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND
            and active.original_watch_id is None
            and transition.status
            in {
                EntryWatchStatus.INVALIDATED,
                EntryWatchStatus.EXPIRED,
            }
        ):
            # A standalone GERI countertrend has no Entry Watcher thesis. Its
            # lifecycle is owned by the GERI setup levels, bars, target and TTL,
            # so an unrelated Core watch must not terminate it.
            return ()
        if transition.status is EntryWatchStatus.INVALIDATED:
            closed = self._close_opportunity(
                active,
                price=transition.current_price,
                now=transition.occurred_at,
                reason=EntryCloseReason.ORIGINAL_THESIS_INVALIDATED,
                leg_status=EntryLegStatus.THESIS_BROKEN,
            )
            closed = _with_source_cursor(
                closed,
                source=_WATCHER_SOURCE,
                occurred_at=transition.occurred_at,
                event_id=transition.transition_id,
            )
            event = self._event(
                closed,
                occurred_at=transition.occurred_at,
                reasons=("original_thesis_invalidated", *transition.reasons),
                event_id=transition.transition_id,
            )
            await self._store.save(closed, event)
            return (event,)
        if transition.status is EntryWatchStatus.EXPIRED:
            closed = self._close_opportunity(
                active,
                price=transition.current_price,
                now=transition.occurred_at,
                reason=EntryCloseReason.EXPIRED,
                leg_status=EntryLegStatus.EXPIRED,
            )
            closed = _with_source_cursor(
                closed,
                source=_WATCHER_SOURCE,
                occurred_at=transition.occurred_at,
                event_id=transition.transition_id,
            )
            event = self._event(
                closed,
                occurred_at=transition.occurred_at,
                reasons=("opportunity_expired", *transition.reasons),
                event_id=transition.transition_id,
            )
            await self._store.save(closed, event)
            return (event,)
        if transition.status is EntryWatchStatus.POLICY_INELIGIBLE:
            return ()

        if (
            transition.status is EntryWatchStatus.TRIGGERED
            and _l2_anchor(active) is not None
            and _rank(active.peak_maturity) < _rank(EntryMaturityLevel.L4)
        ):
            # Once L2 defines a fresh confirmation zone, the watcher must not
            # jump to L4 using the immutable, original ARMED zone.
            return ()

        level = {
            EntryWatchStatus.ARMED: EntryMaturityLevel.ARMED,
            EntryWatchStatus.IN_ZONE: EntryMaturityLevel.IN_ZONE,
            EntryWatchStatus.EARLY_ENTRY: EntryMaturityLevel.L1,
            EntryWatchStatus.IMPULSE_EXTENDED: EntryMaturityLevel.ARMED,
            EntryWatchStatus.TRIGGERED: EntryMaturityLevel.L4,
        }[transition.status]
        changed = self._advance(
            active,
            level=level,
            price=transition.current_price,
            now=transition.occurred_at,
            horizons=transition.horizons,
            source_analysis_ids=transition.source_analysis_ids,
            checkpoint_target=transition.entry_target,
            checkpoint_invalidation=transition.entry_invalidation,
            horizon_invalidations=(
                {horizon: transition.entry_invalidation for horizon in transition.horizons}
                if transition.entry_invalidation is not None
                else None
            ),
            horizon_targets=(
                {horizon: transition.entry_target for horizon in transition.horizons}
                if transition.entry_target is not None
                else None
            ),
            checkpoint_setup_id=f"watch:{transition.watch_id}",
            allow_tracking_regression=self.regress_tracking_maturity,
        )
        changed = _with_source_cursor(
            changed,
            source=_WATCHER_SOURCE,
            occurred_at=transition.occurred_at,
            event_id=transition.transition_id,
        )
        material = (
            changed.peak_maturity is not active.peak_maturity or changed.status is not active.status
        )
        event = self._event(
            changed,
            occurred_at=transition.occurred_at,
            reasons=("opportunity_advanced", *transition.reasons),
            event_id=transition.transition_id,
        )
        await self._store.save(changed, event)
        return (event,) if material else ()

    async def ingest_analysis(
        self, result: AnalysisResult, *, now: datetime
    ) -> tuple[EntryOpportunityEvent, ...]:
        """Update horizon evidence and close only the horizon whose thesis failed."""

        active = await self._store.load_active(result.symbol)
        if active is None or await self._store.event_seen(result.analysis_id):
            return ()
        existing_analysis = next(
            (item for item in active.latest_analyses if item.horizon is result.horizon),
            None,
        )
        if existing_analysis is not None and (
            result.analysis_id == existing_analysis.analysis_id
            or result.as_of < existing_analysis.as_of
            or (
                result.as_of == existing_analysis.as_of
                and result.analysis_id.int <= existing_analysis.analysis_id.int
            )
        ):
            return ()
        price = _metric_decimal(result, "reference_price") or active.current_price
        analyses = _replace_analysis(active.latest_analyses, result)
        sources = _bounded_source_analysis_ids(active.source_analysis_ids, (result.analysis_id,))
        updated = active.model_copy(
            update={
                "current_price": price,
                "updated_at": now,
                "revision": active.revision + 1,
                "latest_analyses": analyses,
                "source_analysis_ids": sources,
            }
        )

        if result.horizon is AnalysisHorizon.INTRADAY:
            updated, retested = _record_l2_retest(updated, result=result, price=price, now=now)
            if retested and _l2_reclaim_confirmed(updated, result=result, price=price):
                anchor = _l2_anchor(updated)
                assert anchor is not None
                invalidation = _metric_decimal(result, "invalidation_level") or anchor.invalidation
                if invalidation >= price:
                    invalidation = anchor.invalidation
                target = _analysis_target(updated.latest_analyses, price) or anchor.target
                changed = self._advance(
                    updated,
                    level=EntryMaturityLevel.L4,
                    price=price,
                    now=now,
                    horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
                    source_analysis_ids=(result.analysis_id,),
                    checkpoint_target=target,
                    checkpoint_invalidation=invalidation,
                    horizon_invalidations={
                        AnalysisHorizon.SWING: invalidation,
                        AnalysisHorizon.INTRADAY: invalidation,
                    },
                    horizon_targets=(
                        {
                            AnalysisHorizon.SWING: target,
                            AnalysisHorizon.INTRADAY: target,
                        }
                        if target is not None
                        else None
                    ),
                )
                event = self._event(
                    changed,
                    occurred_at=now,
                    reasons=(
                        "maturity_l4_reached",
                        "l2_anchor_retested",
                        "l2_zone_reclaim_confirmed",
                        "five_minute_higher_low_confirmed",
                    ),
                    event_id=result.analysis_id,
                )
                await self._store.save(changed, event)
                return (event,)
        original_breached = price <= active.invalidation
        bearish_failure = result.direction is PatternDirection.BEARISH and result.verdict in {
            AnalysisVerdict.AVOID,
            AnalysisVerdict.CAUTION,
        }
        if original_breached or (
            active.primary_signal_family is not EntrySignalFamily.GERI_COUNTERTREND
            and result.horizon is AnalysisHorizon.LONG_TERM
            and (result.verdict is AnalysisVerdict.AVOID or bearish_failure)
        ):
            closed = self._close_opportunity(
                updated,
                price=price,
                now=now,
                reason=EntryCloseReason.ORIGINAL_THESIS_INVALIDATED,
                leg_status=EntryLegStatus.THESIS_BROKEN,
            )
            event = self._event(
                closed,
                occurred_at=now,
                reasons=(
                    "original_invalidation_breached"
                    if original_breached
                    else "long_structure_invalidated",
                ),
                event_id=result.analysis_id,
            )
            await self._store.save(closed, event)
            return (event,)

        if bearish_failure and active.status in {
            EntryOpportunityStatus.CONFIRMING,
            EntryOpportunityStatus.OPEN,
        }:
            changed, closed_leg = self._close_horizon(
                updated,
                horizon=result.horizon,
                price=price,
                now=now,
                status=EntryLegStatus.INVALIDATED,
            )
            if closed_leg:
                reasons = (f"{result.horizon.value.lower()}_invalidated",)
                if _all_opened_legs_terminal(changed.legs):
                    changed = self._close_opportunity(
                        changed,
                        price=price,
                        now=now,
                        reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
                        leg_status=EntryLegStatus.TIME_EXIT,
                    )
                    reasons = (*reasons, "all_horizons_closed")
                event = self._event(
                    changed,
                    occurred_at=now,
                    reasons=reasons,
                    event_id=result.analysis_id,
                )
                await self._store.save(changed, event)
                return (event,)

        await self._store.save(updated, None)
        return ()

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        """Record L1-L4 maturity without replacing the ticker's original thesis."""

        level = _alert_maturity(alert)
        if level is None or await self._store.event_seen(alert.alert_id):
            return ()
        active = await self._store.load_active(alert.symbol)
        source = f"LOCAL_ALERT:{alert.kind.value}"
        if active is None or not _is_new_source_event(
            active,
            source=source,
            occurred_at=alert.created_at,
            event_id=alert.alert_id,
        ):
            return ()
        price = _alert_price(alert) or active.current_price
        horizon_invalidations = {
            horizon: _alert_horizon_level(
                alert,
                horizon,
                ("invalidation", "invalidation_level", "structural_invalidation"),
            )
            or active.invalidation
            for horizon in alert.horizons
        }
        horizon_targets = {
            horizon: target
            for horizon in alert.horizons
            if (
                target := _alert_horizon_level(
                    alert,
                    horizon,
                    ("objective", "target_2r", "objective_level", "take_profit"),
                )
            )
            is not None
            and target > price
        }
        checkpoint_target = min(horizon_targets.values(), default=None)
        checkpoint_invalidation = min(horizon_invalidations.values(), default=active.invalidation)
        checkpoint_zone_low, checkpoint_zone_high = _alert_zone(
            alert,
            price=price,
            invalidation=checkpoint_invalidation,
        )
        changed = self._advance(
            active,
            level=level,
            price=price,
            now=alert.created_at,
            horizons=alert.horizons,
            source_analysis_ids=alert.component_analysis_ids,
            checkpoint_target=checkpoint_target,
            checkpoint_invalidation=checkpoint_invalidation,
            checkpoint_zone_low=(checkpoint_zone_low if level is EntryMaturityLevel.L2 else None),
            checkpoint_zone_high=(checkpoint_zone_high if level is EntryMaturityLevel.L2 else None),
            horizon_invalidations=horizon_invalidations,
            horizon_targets=horizon_targets,
        )
        changed = _with_source_cursor(
            changed,
            source=source,
            occurred_at=alert.created_at,
            event_id=alert.alert_id,
        )
        material = (
            changed.peak_maturity is not active.peak_maturity
            or changed.status is not active.status
            or changed.legs != active.legs
        )
        event = self._event(
            changed,
            occurred_at=alert.created_at,
            reasons=(f"maturity_{level.value.lower()}_reached", *alert.reasons),
            event_id=alert.alert_id,
        )
        await self._store.save(changed, event)
        return (event,) if material else ()

    async def ingest_bar(self, bar: MarketBar) -> tuple[EntryOpportunityEvent, ...]:
        """Mark open paper entries and close the Intraday leg at the regular-session close."""

        if (
            not bar.is_final
            or bar.timeframe is not BarTimeframe.MINUTE_1
            or not is_regular_session(bar.timestamp)
        ):
            return ()
        active = await self._store.load_active(bar.symbol)
        if active is None or bar.timestamp < active.armed_at:
            return ()
        if active.last_market_bar_at is not None and bar.timestamp <= active.last_market_bar_at:
            return ()
        checkpoints = tuple(_mark_checkpoint(item, bar) for item in active.checkpoints)
        legs = tuple(_mark_leg(item, bar) for item in active.legs)
        marked = active.model_copy(update={"checkpoints": checkpoints})
        marked = _record_l2_bar_retest(marked, bar)
        checkpoints = marked.checkpoints
        reasons = _leg_close_reasons(active.legs, legs)
        reasons.extend(_checkpoint_close_reasons(active.checkpoints, checkpoints))

        if bar.low <= active.invalidation:
            updated = active.model_copy(
                update={
                    "checkpoints": checkpoints,
                    "legs": legs,
                    "last_market_bar_at": bar.timestamp,
                }
            )
            closed = self._close_opportunity(
                updated,
                price=active.invalidation,
                now=bar.timestamp,
                reason=EntryCloseReason.ORIGINAL_THESIS_INVALIDATED,
                leg_status=EntryLegStatus.THESIS_BROKEN,
            )
            event = self._event(
                closed,
                occurred_at=bar.timestamp,
                reasons=("original_invalidation_breached",),
            )
            await self._store.save(closed, event)
            return (event,)

        if bar.timeframe is BarTimeframe.MINUTE_1 and _is_regular_close(bar.timestamp):
            next_legs: list[EntryHorizonLeg] = []
            for leg in legs:
                if leg.horizon is AnalysisHorizon.INTRADAY and leg.status is EntryLegStatus.OPEN:
                    next_legs.append(
                        _close_leg(
                            leg,
                            price=bar.close,
                            now=bar.timestamp,
                            status=EntryLegStatus.SESSION_CLOSED,
                        )
                    )
                    reasons.append("intraday_session_closed")
                else:
                    next_legs.append(leg)
            legs = tuple(next_legs)
        updated = active.model_copy(
            update={
                "current_price": bar.close,
                "updated_at": max(active.updated_at, bar.timestamp),
                "last_market_bar_at": bar.timestamp,
                "revision": active.revision + 1,
                "legs": legs,
                "checkpoints": checkpoints,
            }
        )
        if _all_opened_legs_terminal(updated.legs):
            updated = self._close_opportunity(
                updated,
                price=bar.close,
                now=bar.timestamp,
                reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
                leg_status=EntryLegStatus.TIME_EXIT,
            )
            reasons.append("all_horizons_closed")
        event: EntryOpportunityEvent | None = None
        if reasons:
            event = self._event(updated, occurred_at=bar.timestamp, reasons=tuple(reasons))
        await self._store.save(updated, event)
        return (event,) if event is not None else ()

    async def reconcile(
        self, *, now: datetime, active_symbols: Collection[str]
    ) -> tuple[EntryOpportunityEvent, ...]:
        """Close expired and removed opportunities even when no new analysis arrives."""

        normalized = {item.strip().upper() for item in active_symbols}
        events: list[EntryOpportunityEvent] = []
        for active in await self._store.list_active():
            if now >= active.expires_at:
                reason = EntryCloseReason.EXPIRED
                leg_status = EntryLegStatus.EXPIRED
                event_reason = "opportunity_expired"
            elif active.symbol not in normalized:
                reason = EntryCloseReason.UNIVERSE_REMOVED
                leg_status = EntryLegStatus.TIME_EXIT
                event_reason = "symbol_removed_from_universe"
            else:
                continue
            closed = self._close_opportunity(
                active,
                price=active.current_price,
                now=now,
                reason=reason,
                leg_status=leg_status,
            )
            event = self._event(closed, occurred_at=now, reasons=(event_reason,))
            await self._store.save(closed, event)
            events.append(event)
        return tuple(events)

    def _new_opportunity(
        self,
        transition: EntryWatchTransition,
        *,
        level: EntryMaturityLevel,
    ) -> EntryOpportunity:
        checkpoint = self._new_checkpoint(
            level,
            price=transition.current_price,
            invalidation=transition.invalidation,
            reached_at=transition.occurred_at,
            setup_id=f"watch:{transition.watch_id}",
        )
        long_leg = EntryHorizonLeg(
            leg_id=self._child_id_factory(),
            horizon=AnalysisHorizon.LONG_TERM,
            status=EntryLegStatus.WATCHING,
            current_price=transition.current_price,
            invalidation=transition.invalidation,
            highest_price=transition.current_price,
            lowest_price=transition.current_price,
        )
        return EntryOpportunity(
            opportunity_id=self._id_factory(),
            symbol=transition.symbol,
            status=(
                EntryOpportunityStatus.IN_ZONE
                if level is EntryMaturityLevel.IN_ZONE
                else EntryOpportunityStatus.ARMED
            ),
            current_maturity=level,
            peak_maturity=level,
            progress_percent=_PROGRESS[level],
            original_watch_id=transition.watch_id,
            armed_at=transition.occurred_at,
            updated_at=transition.occurred_at,
            expires_at=transition.watch_expires_at,
            zone_low=transition.zone_low,
            zone_high=transition.zone_high,
            invalidation=transition.invalidation,
            original_price=transition.current_price,
            current_price=transition.current_price,
            source_analysis_ids=transition.source_analysis_ids,
            source_cursors=(
                EntryOpportunitySourceCursor(
                    source=_WATCHER_SOURCE,
                    event_id=transition.transition_id,
                    occurred_at=transition.occurred_at,
                ),
            ),
            legs=(long_leg,),
            checkpoints=(checkpoint,),
        )

    def _advance(
        self,
        opportunity: EntryOpportunity,
        *,
        level: EntryMaturityLevel,
        price: Decimal,
        now: datetime,
        horizons: tuple[AnalysisHorizon, ...],
        source_analysis_ids: tuple[UUID, ...],
        checkpoint_target: Decimal | None = None,
        checkpoint_invalidation: Decimal | None = None,
        checkpoint_zone_low: Decimal | None = None,
        checkpoint_zone_high: Decimal | None = None,
        horizon_invalidations: dict[AnalysisHorizon, Decimal] | None = None,
        horizon_targets: dict[AnalysisHorizon, Decimal] | None = None,
        checkpoint_family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY,
        checkpoint_setup_id: str | None = None,
        force_checkpoint: bool = False,
        allow_tracking_regression: bool = False,
    ) -> EntryOpportunity:
        peak_rank = _rank(opportunity.peak_maturity)
        level_rank = _rank(level)
        peak = level if level_rank > peak_rank else opportunity.peak_maturity
        checkpoints = opportunity.checkpoints
        checkpoint_exists = any(
            item.level is level
            and item.signal_family is checkpoint_family
            and item.setup_id == checkpoint_setup_id
            for item in checkpoints
        )
        if (level_rank > peak_rank or force_checkpoint) and not checkpoint_exists:
            checkpoints = (
                *checkpoints,
                self._new_checkpoint(
                    level,
                    price=price,
                    invalidation=checkpoint_invalidation or opportunity.invalidation,
                    reached_at=now,
                    target=checkpoint_target,
                    zone_low=checkpoint_zone_low,
                    zone_high=checkpoint_zone_high,
                    signal_family=checkpoint_family,
                    setup_id=checkpoint_setup_id,
                ),
            )
        status = opportunity.status
        legs = opportunity.legs
        tracking_update = (
            allow_tracking_regression
            and status in {EntryOpportunityStatus.ARMED, EntryOpportunityStatus.IN_ZONE}
            and level in {EntryMaturityLevel.ARMED, EntryMaturityLevel.IN_ZONE}
        )
        if tracking_update:
            status = (
                EntryOpportunityStatus.IN_ZONE
                if level is EntryMaturityLevel.IN_ZONE
                else EntryOpportunityStatus.ARMED
            )
        elif level is EntryMaturityLevel.IN_ZONE and status is EntryOpportunityStatus.ARMED:
            status = EntryOpportunityStatus.IN_ZONE
        if level_rank >= peak_rank and level in {
            EntryMaturityLevel.L1,
            EntryMaturityLevel.L2,
            EntryMaturityLevel.L3,
            EntryMaturityLevel.L4,
        }:
            status = (
                EntryOpportunityStatus.OPEN
                if level is EntryMaturityLevel.L4
                else EntryOpportunityStatus.CONFIRMING
            )
            legs = self._open_horizons(
                legs,
                horizons=horizons,
                price=price,
                invalidation=opportunity.invalidation,
                now=now,
                horizon_invalidations=horizon_invalidations or {},
                horizon_targets=horizon_targets or {},
            )
        return opportunity.model_copy(
            update={
                "status": status,
                "current_maturity": (
                    level
                    if tracking_update or level_rank >= peak_rank
                    else opportunity.current_maturity
                ),
                "peak_maturity": peak,
                "progress_percent": _PROGRESS[level] if tracking_update else _PROGRESS[peak],
                "current_price": (
                    price if now >= opportunity.updated_at else opportunity.current_price
                ),
                "updated_at": max(now, opportunity.updated_at),
                "revision": opportunity.revision + 1,
                "source_analysis_ids": _bounded_source_analysis_ids(
                    opportunity.source_analysis_ids, source_analysis_ids
                ),
                "legs": legs,
                "checkpoints": checkpoints,
            }
        )

    def _new_checkpoint(
        self,
        level: EntryMaturityLevel,
        *,
        price: Decimal,
        invalidation: Decimal,
        reached_at: datetime,
        target: Decimal | None = None,
        zone_low: Decimal | None = None,
        zone_high: Decimal | None = None,
        signal_family: EntrySignalFamily = EntrySignalFamily.CORE_ENTRY,
        setup_id: str | None = None,
    ) -> EntryMaturityCheckpoint:
        return EntryMaturityCheckpoint(
            checkpoint_id=self._child_id_factory(),
            level=level,
            signal_family=signal_family,
            setup_id=setup_id,
            reached_at=reached_at,
            entry_price=price,
            current_price=price,
            highest_price=price,
            lowest_price=price,
            invalidation=invalidation,
            target=target,
            zone_low=zone_low,
            zone_high=zone_high,
        )

    def _open_horizons(
        self,
        legs: tuple[EntryHorizonLeg, ...],
        *,
        horizons: tuple[AnalysisHorizon, ...],
        price: Decimal,
        invalidation: Decimal,
        now: datetime,
        horizon_invalidations: dict[AnalysisHorizon, Decimal],
        horizon_targets: dict[AnalysisHorizon, Decimal],
    ) -> tuple[EntryHorizonLeg, ...]:
        scoped_legs = tuple(item for item in legs if item.setup_id is not None)
        by_horizon = {item.horizon: item for item in legs if item.setup_id is None}
        output: list[EntryHorizonLeg] = []
        for horizon in dict.fromkeys(horizons):
            existing = by_horizon.pop(horizon, None)
            if existing is None:
                existing = EntryHorizonLeg(
                    leg_id=self._child_id_factory(),
                    horizon=horizon,
                    status=EntryLegStatus.WATCHING,
                    current_price=price,
                    invalidation=invalidation,
                    highest_price=price,
                    lowest_price=price,
                )
            if existing.status is EntryLegStatus.WATCHING:
                existing = existing.model_copy(
                    update={
                        "status": EntryLegStatus.OPEN,
                        "opened_at": now,
                        "entry_price": price,
                        "current_price": price,
                        "highest_price": price,
                        "lowest_price": price,
                        "invalidation": horizon_invalidations.get(horizon, invalidation),
                        "target": horizon_targets.get(horizon),
                    }
                )
            output.append(existing)
        output.extend(by_horizon.values())
        output.extend(scoped_legs)
        return tuple(output)

    def _close_horizon(
        self,
        opportunity: EntryOpportunity,
        *,
        horizon: AnalysisHorizon,
        price: Decimal,
        now: datetime,
        status: EntryLegStatus,
    ) -> tuple[EntryOpportunity, bool]:
        closed = False
        legs: list[EntryHorizonLeg] = []
        for leg in opportunity.legs:
            if leg.horizon is horizon and leg.status is EntryLegStatus.OPEN:
                legs.append(_close_leg(leg, price=price, now=now, status=status))
                closed = True
            else:
                legs.append(leg)
        return opportunity.model_copy(
            update={
                "legs": tuple(legs),
                "current_price": price,
                "updated_at": now,
                "revision": opportunity.revision + 1,
            }
        ), closed

    def _close_opportunity(
        self,
        opportunity: EntryOpportunity,
        *,
        price: Decimal,
        now: datetime,
        reason: EntryCloseReason,
        leg_status: EntryLegStatus,
    ) -> EntryOpportunity:
        legs = tuple(
            _close_leg(item, price=price, now=now, status=leg_status)
            if item.status is EntryLegStatus.OPEN
            else item
            for item in opportunity.legs
        )
        checkpoints = tuple(
            _close_checkpoint(item, price=price, now=now, outcome=leg_status)
            if item.status is EntryCheckpointStatus.OPEN
            else item
            for item in opportunity.checkpoints
        )
        return opportunity.model_copy(
            update={
                "status": EntryOpportunityStatus.CLOSED,
                "current_price": (
                    price if now >= opportunity.updated_at else opportunity.current_price
                ),
                "updated_at": max(now, opportunity.updated_at),
                "closed_at": now,
                "close_reason": reason,
                "revision": opportunity.revision + 1,
                "legs": legs,
                "checkpoints": checkpoints,
            }
        )

    @staticmethod
    def _event(
        opportunity: EntryOpportunity,
        *,
        occurred_at: datetime,
        reasons: tuple[str, ...],
        event_id: UUID | None = None,
    ) -> EntryOpportunityEvent:
        return EntryOpportunityEvent(
            event_id=event_id or new_uuid7(),
            occurred_at=occurred_at,
            opportunity=opportunity,
            reasons=reasons,
        )


class EntryOpportunityEngineV2(EntryOpportunityEngine):
    """Consume source-agnostic EntrySignal decisions while retaining paper lifecycle v1."""

    engine_version = "2.0.0"

    async def ingest_signal(self, signal: EntrySignal) -> tuple[EntryOpportunityEvent, ...]:
        """Apply one decision by stable family/setup, independent of its producer."""

        if await self._store.event_seen(signal.signal_id):
            return ()
        active = await self._store.load_active(signal.symbol)
        existing = _signal_reference_for_setup(active, signal) if active is not None else None
        if existing is not None and not _signal_advances_setup(existing, signal):
            return ()

        if active is None:
            if not _has_complete_signal_levels(signal):
                return ()
            opportunity = self._new_signal_opportunity(signal)
            event = self._event(
                opportunity,
                occurred_at=signal.created_at,
                reasons=(
                    "standalone_signal_opportunity_created",
                    f"signal_family_{signal.family.value.lower()}",
                    *signal.reasons,
                ),
                event_id=signal.signal_id,
            )
            await self._store.save(opportunity, event)
            return (event,)

        reference = _signal_reference(signal)
        references = _replace_signal_reference(active.signal_references, reference)
        source_ids = _signal_source_ids(signal)
        if _is_core_signal(signal):
            assert signal.maturity is not None
            if (
                signal.maturity is EntryMaturityLevel.L4
                and _l2_anchor(active) is not None
                and _rank(active.peak_maturity) < _rank(EntryMaturityLevel.L4)
            ):
                return ()
            invalidations = (
                {horizon: signal.invalidation for horizon in signal.horizons}
                if signal.invalidation is not None
                else {}
            )
            target = _first_actionable_target(signal)
            targets = {horizon: target for horizon in signal.horizons} if target is not None else {}
            changed = self._advance(
                active,
                level=signal.maturity,
                price=signal.entry_price,
                now=signal.created_at,
                horizons=signal.horizons,
                source_analysis_ids=source_ids,
                checkpoint_target=target,
                checkpoint_invalidation=signal.invalidation,
                checkpoint_zone_low=(
                    (signal.zone_low or signal.entry_price)
                    if signal.maturity is EntryMaturityLevel.L2
                    else None
                ),
                checkpoint_zone_high=(
                    (signal.zone_high or signal.entry_price)
                    if signal.maturity is EntryMaturityLevel.L2
                    else None
                ),
                horizon_invalidations=invalidations,
                horizon_targets=targets,
                checkpoint_family=signal.family,
                checkpoint_setup_id=signal.setup_id,
                force_checkpoint=signal.family is EntrySignalFamily.CORE_RECOVERY,
            )
        else:
            changed = active.model_copy(
                update={
                    "current_price": (
                        signal.entry_price
                        if signal.created_at >= active.updated_at
                        else active.current_price
                    ),
                    "updated_at": max(signal.created_at, active.updated_at),
                    "revision": active.revision + 1,
                    "source_analysis_ids": _bounded_source_analysis_ids(
                        active.source_analysis_ids, source_ids
                    ),
                }
            )
        changed = changed.model_copy(update={"signal_references": references})
        event = self._event(
            changed,
            occurred_at=signal.created_at,
            reasons=(
                (
                    f"maturity_{signal.maturity.value.lower()}_reached"
                    if signal.maturity is not None
                    else "analytical_signal_registered"
                ),
                f"signal_family_{signal.family.value.lower()}",
                *signal.reasons,
            ),
            event_id=signal.signal_id,
        )
        await self._store.save(changed, event)
        return (event,)

    async def ingest_leveraged_cancellation(
        self,
        assessment: LeveragedThesisAssessment,
    ) -> tuple[EntryOpportunityEvent, ...]:
        """Close only the matching leveraged daily thesis after its support reclaim."""

        if (
            assessment.state is not LeveragedThesisState.CANCELLED
            or assessment.instrument_symbol is None
            or assessment.instrument_bid is None
        ):
            return ()
        if await self._store.event_seen(assessment.assessment_id):
            return ()
        active = await self._store.load_active(assessment.instrument_symbol)
        if active is None or active.primary_signal_family is not EntrySignalFamily.LEVERAGED_THESIS:
            return ()
        setup_id = (
            f"leveraged-thesis:{assessment.underlying_symbol}:"
            f"{assessment.instrument_symbol}:{assessment.occurred_at.date().isoformat()}"
        )
        if not any(
            reference.family is EntrySignalFamily.LEVERAGED_THESIS
            and reference.setup_id == setup_id
            for reference in active.signal_references
        ):
            return ()
        closed = self._close_opportunity(
            active,
            price=assessment.instrument_bid,
            now=assessment.occurred_at,
            reason=EntryCloseReason.SWEEP_RECLAIM_CANCELLED,
            leg_status=EntryLegStatus.THESIS_BROKEN,
        )
        closed = _with_source_cursor(
            closed,
            source=_LEVERAGED_CANCELLATION_SOURCE,
            occurred_at=assessment.occurred_at,
            event_id=assessment.assessment_id,
        )
        event = self._event(
            closed,
            occurred_at=assessment.occurred_at,
            reasons=("leveraged_daily_support_sweep_reclaim", *assessment.reasons),
            event_id=assessment.assessment_id,
        )
        await self._store.save(closed, event)
        return (event,)

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        """Compatibility adapter; new integrations must publish EntrySignal directly."""

        signal = _legacy_signal_from_alert(alert)
        return () if signal is None else await self.ingest_signal(signal)

    def _new_signal_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        assert signal.zone_low is not None
        assert signal.zone_high is not None
        assert signal.invalidation is not None
        analytical = not _is_core_signal(signal)
        initial_level = EntryMaturityLevel.ARMED if analytical else signal.maturity
        assert initial_level is not None
        target = _first_actionable_target(signal)
        checkpoint = self._new_checkpoint(
            initial_level,
            price=signal.entry_price,
            invalidation=signal.invalidation,
            reached_at=signal.created_at,
            target=target,
            signal_family=signal.family,
            setup_id=signal.setup_id,
        )
        targets = {horizon: target for horizon in signal.horizons} if target is not None else {}
        legs = self._open_horizons(
            (),
            horizons=signal.horizons,
            price=signal.entry_price,
            invalidation=signal.invalidation,
            now=signal.created_at,
            horizon_invalidations={horizon: signal.invalidation for horizon in signal.horizons},
            horizon_targets=targets,
        )
        return EntryOpportunity(
            opportunity_id=self._id_factory(),
            symbol=signal.symbol,
            status=(
                EntryOpportunityStatus.OPEN
                if analytical or signal.maturity is EntryMaturityLevel.L4
                else EntryOpportunityStatus.CONFIRMING
            ),
            current_maturity=initial_level,
            peak_maturity=initial_level,
            progress_percent=(Decimal("100") if analytical else _PROGRESS[initial_level]),
            original_watch_id=None,
            armed_at=signal.created_at,
            updated_at=signal.created_at,
            expires_at=signal.created_at + _signal_lifetime(signal),
            zone_low=signal.zone_low,
            zone_high=signal.zone_high,
            invalidation=signal.invalidation,
            original_price=signal.entry_price,
            current_price=signal.entry_price,
            source_analysis_ids=_signal_source_ids(signal),
            primary_signal_family=signal.family,
            signal_references=(_signal_reference(signal),),
            legs=legs,
            checkpoints=(checkpoint,),
        )


class EntryOpportunityEngineV3(EntryOpportunityEngineV2):
    """Reflect watcher ARMED/IN_ZONE regressions while retaining peak maturity."""

    engine_version = "3.0.0"
    regress_tracking_maturity = True


class EntryOpportunityEngineV4(EntryOpportunityEngineV3):
    """Track SwingTrade ST1-ST4 independently from Core L1-L4."""

    engine_version = "4.0.0"
    allow_parallel_swing_trade_legs = False

    async def ingest_signal(self, signal: EntrySignal) -> tuple[EntryOpportunityEvent, ...]:
        if signal.family is not EntrySignalFamily.SWING_TRADE:
            return await super().ingest_signal(signal)
        if await self._store.event_seen(signal.signal_id):
            return ()
        active = await self._store.load_active(signal.symbol)
        if active is None:
            if signal.swing_trade_maturity is None or not _has_complete_signal_levels(signal):
                return ()
            changed = self._new_swing_trade_opportunity(signal)
            reason = "swing_trade_tracking_created"
        else:
            changed, reason = self._apply_swing_trade(active, signal)
            if changed is None:
                return ()
        event = self._event(
            changed,
            occurred_at=signal.created_at,
            reasons=(reason, *signal.reasons),
            event_id=signal.signal_id,
        )
        await self._store.save(changed, event)
        return (event,)

    def _new_swing_trade_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        assert signal.zone_low is not None
        assert signal.zone_high is not None
        assert signal.invalidation is not None
        assert signal.swing_trade_maturity is not None
        stage = signal.swing_trade_maturity
        target = _first_actionable_target(signal)
        entered = _st_rank(stage) >= _st_rank(SwingTradeMaturity.ST3)
        leg = EntryHorizonLeg(
            horizon=AnalysisHorizon.SWING,
            setup_id=(signal.setup_id if self.allow_parallel_swing_trade_legs else None),
            status=EntryLegStatus.OPEN if entered else EntryLegStatus.WATCHING,
            opened_at=signal.created_at if entered else None,
            expires_at=(
                _add_weekdays(signal.created_at, 10)
                if entered and self.allow_parallel_swing_trade_legs
                else None
            ),
            entry_price=signal.entry_price if entered else None,
            current_price=signal.entry_price,
            invalidation=signal.invalidation,
            target=target,
            highest_price=signal.entry_price,
            lowest_price=signal.entry_price,
        )
        checkpoint = _swing_trade_checkpoint(signal, stage, self._child_id_factory())
        return EntryOpportunity(
            opportunity_id=self._id_factory(),
            symbol=signal.symbol,
            status=EntryOpportunityStatus.OPEN if entered else EntryOpportunityStatus.CONFIRMING,
            current_maturity=EntryMaturityLevel.ARMED,
            peak_maturity=EntryMaturityLevel.ARMED,
            progress_percent=_st_progress(stage),
            armed_at=signal.created_at,
            updated_at=signal.created_at,
            expires_at=_add_weekdays(signal.created_at, 10),
            zone_low=signal.zone_low,
            zone_high=signal.zone_high,
            invalidation=signal.invalidation,
            original_price=signal.entry_price,
            current_price=signal.entry_price,
            source_analysis_ids=_signal_source_ids(signal),
            primary_signal_family=EntrySignalFamily.SWING_TRADE,
            signal_references=(_swing_trade_reference(signal, None),),
            legs=(leg,),
            checkpoints=(checkpoint,),
        )

    def _apply_swing_trade(
        self, active: EntryOpportunity, signal: EntrySignal
    ) -> tuple[EntryOpportunity | None, str]:
        previous = _signal_reference_for_setup(active, signal)
        if previous is not None and previous.signal_id == signal.signal_id:
            return None, "duplicate"
        paper_open = self._swing_trade_paper_open(active, setup_id=signal.setup_id)
        has_other_swing_setup = any(
            item.family is EntrySignalFamily.SWING_TRADE
            and item.setup_id != signal.setup_id
            for item in active.signal_references
        )
        if (
            not self.allow_parallel_swing_trade_legs
            and paper_open
            and previous is None
            and has_other_swing_setup
        ):
            return None, "new_setup_ignored_while_paper_open"
        if signal.swing_trade_maturity is None:
            if paper_open:
                reference = _swing_trade_reference(signal, previous)
                return active.model_copy(
                    update={
                        "signal_references": _replace_signal_reference(
                            active.signal_references, reference
                        ),
                        "updated_at": max(active.updated_at, signal.created_at),
                        "revision": active.revision + 1,
                    }
                ), "swing_trade_tracking_lost_after_entry"
            if (
                self.allow_parallel_swing_trade_legs
                and previous is not None
                and any(leg.status is EntryLegStatus.OPEN for leg in active.legs)
            ):
                reference = _swing_trade_reference(signal, previous)
                checkpoints = tuple(
                    _close_checkpoint(
                        item,
                        price=signal.entry_price,
                        now=signal.created_at,
                        outcome=EntryLegStatus.THESIS_BROKEN,
                    )
                    if item.signal_family is EntrySignalFamily.SWING_TRADE
                    and item.setup_id == signal.setup_id
                    and item.status is EntryCheckpointStatus.OPEN
                    else item
                    for item in active.checkpoints
                )
                return active.model_copy(
                    update={
                        "signal_references": _replace_signal_reference(
                            active.signal_references, reference
                        ),
                        "checkpoints": checkpoints,
                        "current_price": signal.entry_price,
                        "updated_at": max(active.updated_at, signal.created_at),
                        "revision": active.revision + 1,
                    }
                ), "swing_trade_parallel_preentry_ineligible"
            if active.primary_signal_family is EntrySignalFamily.SWING_TRADE:
                return self._close_opportunity(
                    active,
                    price=signal.entry_price,
                    now=signal.created_at,
                    reason=EntryCloseReason.POLICY_INELIGIBLE,
                    leg_status=EntryLegStatus.THESIS_BROKEN,
                ), "swing_trade_preentry_ineligible"
            reference = _swing_trade_reference(signal, previous)
            return active.model_copy(
                update={
                    "signal_references": _replace_signal_reference(
                        active.signal_references, reference
                    ),
                    "updated_at": max(active.updated_at, signal.created_at),
                    "revision": active.revision + 1,
                }
            ), "swing_trade_confluence_removed"

        stage = signal.swing_trade_maturity
        if not _has_complete_signal_levels(signal):
            return None, "incomplete_levels"
        assert signal.invalidation is not None
        prior_peak = previous.peak_st if previous is not None else None
        if prior_peak is not None and _st_rank(stage) <= _st_rank(prior_peak):
            if previous is not None and previous.current_st is stage:
                return None, "unchanged_stage"
            reference = _swing_trade_reference(signal, previous)
            return active.model_copy(
                update={
                    "signal_references": _replace_signal_reference(
                        active.signal_references, reference
                    ),
                    "current_price": signal.entry_price,
                    "updated_at": max(active.updated_at, signal.created_at),
                    "revision": active.revision + 1,
                }
            ), f"swing_trade_current_{stage.value.lower()}"
        reference = _swing_trade_reference(signal, previous)
        checkpoints = active.checkpoints
        if not any(
            item.signal_family is EntrySignalFamily.SWING_TRADE
            and item.setup_id == signal.setup_id
            and item.swing_trade_maturity is stage
            for item in checkpoints
        ):
            checkpoints = (
                *checkpoints,
                _swing_trade_checkpoint(signal, stage, self._child_id_factory()),
            )
        legs = active.legs
        entered_now = (
            _st_rank(stage) >= _st_rank(SwingTradeMaturity.ST3)
            and not paper_open
            and not (
                self.allow_parallel_swing_trade_legs
                and _swing_trade_setup_entered(active, setup_id=signal.setup_id)
            )
        )
        leg_expires_at: datetime | None = None
        if entered_now:
            target = _first_actionable_target(signal)
            leg_expires_at = _add_weekdays(signal.created_at, 10)
            found = False
            updated_legs: list[EntryHorizonLeg] = []
            for leg in legs:
                if (
                    leg.horizon is AnalysisHorizon.SWING
                    and leg.status is EntryLegStatus.WATCHING
                    and (
                        not self.allow_parallel_swing_trade_legs
                        or _swing_trade_leg_matches_setup(
                            active, leg, setup_id=signal.setup_id
                        )
                    )
                ):
                    updated_legs.append(
                        leg.model_copy(
                            update={
                                "setup_id": (
                                    signal.setup_id
                                    if self.allow_parallel_swing_trade_legs
                                    else leg.setup_id
                                ),
                                "status": EntryLegStatus.OPEN,
                                "opened_at": signal.created_at,
                                "expires_at": (
                                    leg_expires_at
                                    if self.allow_parallel_swing_trade_legs
                                    else leg.expires_at
                                ),
                                "entry_price": signal.entry_price,
                                "current_price": signal.entry_price,
                                "invalidation": signal.invalidation,
                                "target": target,
                                "highest_price": signal.entry_price,
                                "lowest_price": signal.entry_price,
                            }
                        )
                    )
                    found = True
                else:
                    updated_legs.append(leg)
            if not found:
                updated_legs.append(
                    EntryHorizonLeg(
                        horizon=AnalysisHorizon.SWING,
                        setup_id=(
                            signal.setup_id if self.allow_parallel_swing_trade_legs else None
                        ),
                        status=EntryLegStatus.OPEN,
                        opened_at=signal.created_at,
                        expires_at=(
                            leg_expires_at if self.allow_parallel_swing_trade_legs else None
                        ),
                        entry_price=signal.entry_price,
                        current_price=signal.entry_price,
                        invalidation=signal.invalidation,
                        target=target,
                        highest_price=signal.entry_price,
                        lowest_price=signal.entry_price,
                    )
                )
            legs = tuple(updated_legs)
            if self.allow_parallel_swing_trade_legs:
                legs = _label_legacy_swing_trade_leg(active, legs)
                legs = tuple(
                    item.model_copy(update={"expires_at": active.expires_at})
                    if item.status is EntryLegStatus.OPEN and item.expires_at is None
                    else item
                    for item in legs
                )
        updates: dict[str, object] = {
            "signal_references": _replace_signal_reference(active.signal_references, reference),
            "source_analysis_ids": _bounded_source_analysis_ids(
                active.source_analysis_ids, _signal_source_ids(signal)
            ),
            "current_price": signal.entry_price,
            "updated_at": max(active.updated_at, signal.created_at),
            "revision": active.revision + 1,
            "checkpoints": checkpoints,
            "legs": legs,
        }
        if entered_now:
            assert leg_expires_at is not None
            updates["status"] = EntryOpportunityStatus.OPEN
            updates["expires_at"] = max(active.expires_at, leg_expires_at)
            if self.allow_parallel_swing_trade_legs:
                updates["invalidation"] = min(active.invalidation, signal.invalidation)
        if active.primary_signal_family is EntrySignalFamily.SWING_TRADE:
            updates["progress_percent"] = (
                max(active.progress_percent, _st_progress(stage))
                if self.allow_parallel_swing_trade_legs
                else _st_progress(stage)
            )
        return active.model_copy(update=updates), f"swing_trade_{stage.value.lower()}_reached"

    def _swing_trade_paper_open(self, active: EntryOpportunity, *, setup_id: str) -> bool:
        if not self.allow_parallel_swing_trade_legs:
            return any(leg.status is EntryLegStatus.OPEN for leg in active.legs)
        return any(
            leg.status is EntryLegStatus.OPEN
            and _swing_trade_leg_matches_setup(active, leg, setup_id=setup_id)
            for leg in active.legs
        )


class EntryOpportunityEngineV5(EntryOpportunityEngineV4):
    """Paper-track GERI countertrend CT0-CT4 without changing Core or ST maturity."""

    engine_version = "5.0.0"

    async def ingest_signal(self, signal: EntrySignal) -> tuple[EntryOpportunityEvent, ...]:
        if signal.family is not EntrySignalFamily.GERI_COUNTERTREND:
            return await super().ingest_signal(signal)
        if await self._store.event_seen(signal.signal_id):
            return ()
        active = await self._store.load_active(signal.symbol)
        if active is None:
            if not is_regular_session(signal.created_at):
                return ()
            if signal.countertrend_maturity is None or not _has_complete_signal_levels(signal):
                return ()
            changed = self._new_countertrend_opportunity(signal)
            reason = "geri_countertrend_tracking_created"
        else:
            changed, reason = self._apply_countertrend(active, signal)
            if changed is None:
                return ()
        event = self._event(
            changed,
            occurred_at=signal.created_at,
            reasons=(reason, *signal.reasons),
            event_id=signal.signal_id,
        )
        await self._store.save(changed, event)
        return (event,)

    def _new_countertrend_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        assert signal.zone_low is not None
        assert signal.zone_high is not None
        assert signal.invalidation is not None
        assert signal.countertrend_maturity is not None
        stage = signal.countertrend_maturity
        entered = _ct_rank(stage) >= _ct_rank(GeriCountertrendMaturity.CT1)
        target = _first_actionable_target(signal)
        leg = EntryHorizonLeg(
            horizon=AnalysisHorizon.SWING,
            status=EntryLegStatus.OPEN if entered else EntryLegStatus.WATCHING,
            opened_at=signal.created_at if entered else None,
            entry_price=signal.entry_price if entered else None,
            current_price=signal.entry_price,
            invalidation=signal.invalidation,
            target=target,
            highest_price=signal.entry_price,
            lowest_price=signal.entry_price,
        )
        return EntryOpportunity(
            opportunity_id=self._id_factory(),
            symbol=signal.symbol,
            status=EntryOpportunityStatus.OPEN if entered else EntryOpportunityStatus.CONFIRMING,
            current_maturity=EntryMaturityLevel.ARMED,
            peak_maturity=EntryMaturityLevel.ARMED,
            progress_percent=_ct_progress(stage),
            armed_at=signal.created_at,
            updated_at=signal.created_at,
            expires_at=_add_weekdays(signal.created_at, 5),
            zone_low=signal.zone_low,
            zone_high=signal.zone_high,
            invalidation=signal.invalidation,
            original_price=signal.entry_price,
            current_price=signal.entry_price,
            source_analysis_ids=_signal_source_ids(signal),
            primary_signal_family=EntrySignalFamily.GERI_COUNTERTREND,
            signal_references=(_countertrend_reference(signal, None),),
            legs=(leg,),
            checkpoints=(
                _countertrend_checkpoint(signal, stage, self._child_id_factory()),
            ),
        )

    def _apply_countertrend(
        self, active: EntryOpportunity, signal: EntrySignal
    ) -> tuple[EntryOpportunity | None, str]:
        previous = _signal_reference_for_setup(active, signal)
        if previous is not None and previous.signal_id == signal.signal_id:
            return None, "duplicate"
        paper_open = _countertrend_paper_open(active, setup_id=signal.setup_id)
        has_other_setup = any(
            item.family is EntrySignalFamily.GERI_COUNTERTREND
            and item.setup_id != signal.setup_id
            for item in active.signal_references
        )
        if (
            previous is None
            and has_other_setup
            and _any_countertrend_paper_open(active)
        ):
            return None, "new_countertrend_setup_ignored_while_paper_open"
        if signal.countertrend_maturity is None:
            return self._apply_countertrend_loss(active, signal, previous, paper_open)
        if not _has_complete_signal_levels(signal):
            return None, "incomplete_levels"
        stage = signal.countertrend_maturity
        prior_peak = previous.peak_ct if previous is not None else None
        if prior_peak is not None and _ct_rank(stage) <= _ct_rank(prior_peak):
            if previous is not None and previous.current_ct is stage:
                return None, "unchanged_stage"
            reference = _countertrend_reference(signal, previous)
            return active.model_copy(
                update={
                    "signal_references": _replace_signal_reference(
                        active.signal_references, reference
                    ),
                    "current_price": signal.entry_price,
                    "updated_at": max(active.updated_at, signal.created_at),
                    "revision": active.revision + 1,
                }
            ), f"geri_countertrend_current_{stage.value.lower()}"

        reference = _countertrend_reference(signal, previous)
        checkpoints = active.checkpoints
        if not any(
            item.signal_family is EntrySignalFamily.GERI_COUNTERTREND
            and item.setup_id == signal.setup_id
            and item.countertrend_maturity is stage
            for item in checkpoints
        ):
            checkpoints = (
                *checkpoints,
                _countertrend_checkpoint(signal, stage, self._child_id_factory()),
            )
        entered_now = (
            _ct_rank(stage) >= _ct_rank(GeriCountertrendMaturity.CT1) and not paper_open
        )
        legs = active.legs
        if entered_now and active.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
            legs = _open_countertrend_leg(active, signal)
        updates: dict[str, object] = {
            "signal_references": _replace_signal_reference(
                active.signal_references, reference
            ),
            "source_analysis_ids": _bounded_source_analysis_ids(
                active.source_analysis_ids, _signal_source_ids(signal)
            ),
            "current_price": signal.entry_price,
            "updated_at": max(active.updated_at, signal.created_at),
            "revision": active.revision + 1,
            "checkpoints": checkpoints,
            "legs": legs,
        }
        if active.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
            updates["progress_percent"] = _ct_progress(stage)
            if entered_now:
                updates["status"] = EntryOpportunityStatus.OPEN
        return active.model_copy(update=updates), f"geri_countertrend_{stage.value.lower()}_reached"

    def _apply_countertrend_loss(
        self,
        active: EntryOpportunity,
        signal: EntrySignal,
        previous: EntryOpportunitySignalReference | None,
        paper_open: bool,
    ) -> tuple[EntryOpportunity | None, str]:
        if previous is None:
            return None, "unknown_setup"
        reference = _countertrend_reference(signal, previous)
        terminal = _countertrend_terminal(signal)
        if active.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
            if terminal is not None:
                price, close_reason, leg_status, reason = terminal
                return self._close_opportunity(
                    active,
                    price=price,
                    now=signal.created_at,
                    reason=close_reason,
                    leg_status=leg_status,
                ), reason
            if not paper_open:
                favorable_entry_loss = any(
                    reason in {"insufficient_reward_risk", "countertrend_extended"}
                    for reason in signal.reasons
                )
                if favorable_entry_loss and "regular_session_close" not in signal.reasons:
                    reference = reference.model_copy(update={"current_ct": previous.current_ct})
                    return active.model_copy(
                        update={
                            "signal_references": _replace_signal_reference(
                                active.signal_references, reference
                            ),
                            "current_price": signal.entry_price,
                            "updated_at": max(active.updated_at, signal.created_at),
                            "revision": active.revision + 1,
                        }
                    ), "geri_countertrend_preentry_ineligible_deferred"
                at_session_close = "regular_session_close" in signal.reasons
                return self._close_opportunity(
                    active,
                    price=signal.entry_price,
                    now=signal.created_at,
                    reason=EntryCloseReason.POLICY_INELIGIBLE,
                    leg_status=(
                        EntryLegStatus.TIME_EXIT
                        if at_session_close
                        else EntryLegStatus.THESIS_BROKEN
                    ),
                ), (
                    "geri_countertrend_preentry_ineligible_at_session_close"
                    if at_session_close
                    else "geri_countertrend_preentry_ineligible"
                )
            return active.model_copy(
                update={
                    "signal_references": _replace_signal_reference(
                        active.signal_references, reference
                    ),
                    "current_price": signal.entry_price,
                    "updated_at": max(active.updated_at, signal.created_at),
                    "revision": active.revision + 1,
                }
            ), "geri_countertrend_tracking_lost_after_entry"

        outcome = terminal
        reason = "geri_countertrend_tracking_lost_after_entry"
        if outcome is None and not paper_open:
            favorable_entry_loss = any(
                item in {"insufficient_reward_risk", "countertrend_extended"}
                for item in signal.reasons
            )
            at_session_close = "regular_session_close" in signal.reasons
            if favorable_entry_loss and not at_session_close:
                reference = reference.model_copy(update={"current_ct": previous.current_ct})
                reason = "geri_countertrend_preentry_ineligible_deferred"
            else:
                outcome = (
                    signal.entry_price,
                    EntryCloseReason.POLICY_INELIGIBLE,
                    (
                        EntryLegStatus.TIME_EXIT
                        if at_session_close
                        else EntryLegStatus.THESIS_BROKEN
                    ),
                    (
                        "geri_countertrend_preentry_ineligible_at_session_close"
                        if at_session_close
                        else "geri_countertrend_preentry_ineligible"
                    ),
                )
        checkpoints = active.checkpoints
        if outcome is not None:
            price, _, leg_status, reason = outcome
            checkpoints = tuple(
                _close_checkpoint(
                    item,
                    price=price,
                    now=signal.created_at,
                    outcome=leg_status,
                )
                if item.signal_family is EntrySignalFamily.GERI_COUNTERTREND
                and item.setup_id == signal.setup_id
                and item.status is EntryCheckpointStatus.OPEN
                else item
                for item in checkpoints
            )
        return active.model_copy(
            update={
                "signal_references": _replace_signal_reference(
                    active.signal_references, reference
                ),
                "current_price": signal.entry_price,
                "updated_at": max(active.updated_at, signal.created_at),
                "revision": active.revision + 1,
                "checkpoints": checkpoints,
            }
        ), reason


class EntryOpportunityEngineV6(EntryOpportunityEngineV5):
    """Track independent SwingTrade legs when a new version emits a new thesis."""

    engine_version = "6.0.0"
    allow_parallel_swing_trade_legs = True

    async def reconcile(
        self, *, now: datetime, active_symbols: Collection[str]
    ) -> tuple[EntryOpportunityEvent, ...]:
        normalized = {item.strip().upper() for item in active_symbols}
        events: list[EntryOpportunityEvent] = []
        for active in await self._store.list_active():
            if active.symbol not in normalized:
                closed = self._close_opportunity(
                    active,
                    price=active.current_price,
                    now=now,
                    reason=EntryCloseReason.UNIVERSE_REMOVED,
                    leg_status=EntryLegStatus.TIME_EXIT,
                )
                event = self._event(
                    closed, occurred_at=now, reasons=("symbol_removed_from_universe",)
                )
                await self._store.save(closed, event)
                events.append(event)
                continue

            legs: list[EntryHorizonLeg] = []
            expired_setups: set[str] = set()
            expired_horizons: set[AnalysisHorizon] = set()
            for leg in active.legs:
                deadline = leg.expires_at or active.expires_at
                if (
                    now < deadline
                    or leg.status in _TERMINAL_LEGS
                ):
                    legs.append(leg)
                    continue
                expired_horizons.add(leg.horizon)
                if leg.setup_id is not None:
                    expired_setups.add(leg.setup_id)
                if leg.status is EntryLegStatus.OPEN:
                    legs.append(
                        _close_leg(
                            leg,
                            price=leg.current_price,
                            now=now,
                            status=EntryLegStatus.EXPIRED,
                        )
                    )

            if not expired_horizons:
                continue

            checkpoints = tuple(
                _close_checkpoint(
                    item,
                    price=item.current_price,
                    now=now,
                    outcome=EntryLegStatus.EXPIRED,
                )
                if item.setup_id in expired_setups
                and item.status is EntryCheckpointStatus.OPEN
                else item
                for item in active.checkpoints
            )
            updated = active.model_copy(
                update={
                    "legs": tuple(legs),
                    "checkpoints": checkpoints,
                    "updated_at": max(active.updated_at, now),
                    "revision": active.revision + 1,
                }
            )
            if not any(
                leg.status in {EntryLegStatus.WATCHING, EntryLegStatus.OPEN}
                for leg in updated.legs
            ):
                updated = self._close_opportunity(
                    updated,
                    price=active.current_price,
                    now=now,
                    reason=EntryCloseReason.EXPIRED,
                    leg_status=EntryLegStatus.EXPIRED,
                )
                reasons = ("opportunity_expired",)
            else:
                deadlines = tuple(
                    leg.expires_at or active.expires_at
                    for leg in updated.legs
                    if leg.status in {EntryLegStatus.WATCHING, EntryLegStatus.OPEN}
                )
                updated = updated.model_copy(update={"expires_at": max(deadlines)})
                reasons = tuple(
                    f"{horizon.value.lower()}_leg_expired"
                    for horizon in sorted(expired_horizons, key=lambda item: item.value)
                )
            event = self._event(updated, occurred_at=now, reasons=reasons)
            await self._store.save(updated, event)
            events.append(event)
        return tuple(events)


class EntryOpportunityEngineV7(EntryOpportunityEngineV6):
    """Update an existing SwingTrade thesis when only its policy version changed."""

    engine_version = "7.0.0"

    def _new_swing_trade_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        return super()._new_swing_trade_opportunity(_canonical_swing_trade_signal(signal))

    def _apply_swing_trade(
        self, active: EntryOpportunity, signal: EntrySignal
    ) -> tuple[EntryOpportunity | None, str]:
        normalized = _consolidate_swing_trade_theses(active)
        canonical_signal = _canonical_swing_trade_signal(signal)
        changed, reason = super()._apply_swing_trade(normalized, canonical_signal)
        if changed is not None or normalized == active:
            return changed, reason

        previous = _signal_reference_for_setup(normalized, canonical_signal)
        reference = _swing_trade_reference(canonical_signal, previous)
        return normalized.model_copy(
            update={
                "signal_references": _replace_signal_reference(
                    normalized.signal_references, reference
                ),
                "source_analysis_ids": _bounded_source_analysis_ids(
                    normalized.source_analysis_ids,
                    _signal_source_ids(canonical_signal),
                ),
                "current_price": canonical_signal.entry_price,
                "updated_at": max(normalized.updated_at, canonical_signal.created_at),
                "revision": active.revision + 1,
            }
        ), "swing_trade_equivalent_thesis_updated"


class EntryOpportunityEngineV8(EntryOpportunityEngineV7):
    """Preserve SwingTrade structure while pre-entry policy gates fluctuate."""

    engine_version = "8.0.0"

    def _apply_swing_trade(
        self, active: EntryOpportunity, signal: EntrySignal
    ) -> tuple[EntryOpportunity | None, str]:
        canonical_signal = _canonical_swing_trade_signal(signal)
        if canonical_signal.swing_trade_maturity is not None:
            return super()._apply_swing_trade(active, signal)

        normalized = _consolidate_swing_trade_theses(active)
        previous = _signal_reference_for_setup(normalized, canonical_signal)
        if previous is None:
            return None, "untracked_swing_trade_ineligible"

        reference = _swing_trade_reference(canonical_signal, previous)
        updated = normalized.model_copy(
            update={
                "signal_references": _replace_signal_reference(
                    normalized.signal_references, reference
                ),
                "source_analysis_ids": _bounded_source_analysis_ids(
                    normalized.source_analysis_ids,
                    _signal_source_ids(canonical_signal),
                ),
                "current_price": canonical_signal.entry_price,
                "updated_at": max(normalized.updated_at, canonical_signal.created_at),
                "revision": active.revision + 1,
            }
        )
        if self._swing_trade_paper_open(
            normalized, setup_id=canonical_signal.setup_id
        ):
            return updated, "swing_trade_tracking_lost_after_entry"
        if normalized.primary_signal_family is EntrySignalFamily.SWING_TRADE:
            return updated, "swing_trade_preentry_ineligible_deferred"
        return updated, "swing_trade_confluence_removed"


_SEMVER_SUFFIX = re.compile(r"\d+\.\d+\.\d+")


def _canonical_swing_trade_setup_id(setup_id: str) -> str:
    if not setup_id.startswith("swing-trade:") or ":" not in setup_id:
        return setup_id
    structural_id, version = setup_id.rsplit(":", 1)
    return structural_id if _SEMVER_SUFFIX.fullmatch(version) else setup_id


def _canonical_swing_trade_signal(signal: EntrySignal) -> EntrySignal:
    if signal.family is not EntrySignalFamily.SWING_TRADE:
        return signal
    setup_id = _canonical_swing_trade_setup_id(signal.setup_id)
    if setup_id == signal.setup_id:
        return signal
    return signal.model_copy(update={"setup_id": setup_id})


def _consolidate_swing_trade_theses(active: EntryOpportunity) -> EntryOpportunity:
    references = _consolidate_swing_trade_references(active.signal_references)
    checkpoints = _consolidate_swing_trade_checkpoints(active.checkpoints)
    legs = _consolidate_swing_trade_legs(active.legs)
    if (
        references == active.signal_references
        and checkpoints == active.checkpoints
        and legs == active.legs
    ):
        return active
    return active.model_copy(
        update={
            "signal_references": references,
            "checkpoints": checkpoints,
            "legs": legs,
        }
    )


def _consolidate_swing_trade_references(
    references: tuple[EntryOpportunitySignalReference, ...],
) -> tuple[EntryOpportunitySignalReference, ...]:
    values: list[EntryOpportunitySignalReference] = []
    positions: dict[str, int] = {}
    for item in references:
        if item.family is not EntrySignalFamily.SWING_TRADE:
            values.append(item)
            continue
        setup_id = _canonical_swing_trade_setup_id(item.setup_id)
        normalized = item.model_copy(update={"setup_id": setup_id})
        position = positions.get(setup_id)
        if position is None:
            positions[setup_id] = len(values)
            values.append(normalized)
            continue
        existing = values[position]
        latest = normalized if normalized.created_at >= existing.created_at else existing
        peaks = tuple(
            value
            for value in (existing.peak_st, normalized.peak_st)
            if value is not None
        )
        peak = max(peaks, key=_st_rank) if peaks else None
        values[position] = latest.model_copy(update={"setup_id": setup_id, "peak_st": peak})
    return tuple(values)


def _consolidate_swing_trade_checkpoints(
    checkpoints: tuple[EntryMaturityCheckpoint, ...],
) -> tuple[EntryMaturityCheckpoint, ...]:
    values: list[EntryMaturityCheckpoint] = []
    positions: dict[tuple[EntryMaturityLevel, SwingTradeMaturity | None, str], int] = {}
    for item in checkpoints:
        if item.signal_family is not EntrySignalFamily.SWING_TRADE or item.setup_id is None:
            values.append(item)
            continue
        setup_id = _canonical_swing_trade_setup_id(item.setup_id)
        normalized = item.model_copy(update={"setup_id": setup_id})
        key = (normalized.level, normalized.swing_trade_maturity, setup_id)
        position = positions.get(key)
        if position is None:
            positions[key] = len(values)
            values.append(normalized)
            continue
        existing = values[position]
        earliest = existing if existing.reached_at <= normalized.reached_at else normalized
        latest = normalized if normalized.reached_at >= existing.reached_at else existing
        values[position] = earliest.model_copy(
            update={
                "setup_id": setup_id,
                "current_price": latest.current_price,
                "highest_price": max(existing.highest_price, normalized.highest_price),
                "lowest_price": min(existing.lowest_price, normalized.lowest_price),
                "mfe_percent": max(existing.mfe_percent, normalized.mfe_percent),
                "mae_percent": min(existing.mae_percent, normalized.mae_percent),
            }
        )
    return tuple(values)


def _consolidate_swing_trade_legs(
    legs: tuple[EntryHorizonLeg, ...],
) -> tuple[EntryHorizonLeg, ...]:
    values: list[EntryHorizonLeg] = []
    positions: dict[tuple[AnalysisHorizon, str], int] = {}
    for item in legs:
        if item.horizon is not AnalysisHorizon.SWING or item.setup_id is None:
            values.append(item)
            continue
        setup_id = _canonical_swing_trade_setup_id(item.setup_id)
        normalized = item.model_copy(update={"setup_id": setup_id})
        key = (normalized.horizon, setup_id)
        position = positions.get(key)
        if position is None:
            positions[key] = len(values)
            values.append(normalized)
            continue
        existing = values[position]
        values[position] = _preferred_equivalent_swing_leg(existing, normalized)
    return tuple(values)


def _preferred_equivalent_swing_leg(
    existing: EntryHorizonLeg, candidate: EntryHorizonLeg
) -> EntryHorizonLeg:
    rank = {
        EntryLegStatus.OPEN: 3,
        EntryLegStatus.WATCHING: 2,
    }
    existing_rank = rank.get(existing.status, 1)
    candidate_rank = rank.get(candidate.status, 1)
    if candidate_rank != existing_rank:
        return candidate if candidate_rank > existing_rank else existing
    if existing.opened_at is None or candidate.opened_at is None:
        return existing if existing.opened_at is None else candidate
    return existing if existing.opened_at <= candidate.opened_at else candidate


def _is_core_signal(signal: EntrySignal) -> bool:
    return signal.family in {
        EntrySignalFamily.CORE_ENTRY,
        EntrySignalFamily.CORE_RECOVERY,
    }


def _has_complete_signal_levels(signal: EntrySignal) -> bool:
    return (
        signal.zone_low is not None
        and signal.zone_high is not None
        and signal.invalidation is not None
    )


def _signal_reference(signal: EntrySignal) -> EntryOpportunitySignalReference:
    return EntryOpportunitySignalReference(
        signal_id=signal.signal_id,
        family=signal.family,
        maturity=signal.maturity,
        current_st=signal.swing_trade_maturity,
        peak_st=signal.swing_trade_maturity,
        setup_id=signal.setup_id,
        created_at=signal.created_at,
        entry_price=signal.entry_price,
        horizons=signal.horizons,
        policy_id=signal.policy_id,
        policy_version=signal.policy_version,
    )


def _swing_trade_reference(
    signal: EntrySignal,
    previous: EntryOpportunitySignalReference | None,
) -> EntryOpportunitySignalReference:
    peak = signal.swing_trade_maturity
    if (
        previous is not None
        and previous.peak_st is not None
        and (peak is None or _st_rank(previous.peak_st) > _st_rank(peak))
    ):
        peak = previous.peak_st
    return EntryOpportunitySignalReference(
        signal_id=signal.signal_id,
        family=signal.family,
        setup_id=signal.setup_id,
        created_at=signal.created_at,
        entry_price=signal.entry_price,
        horizons=signal.horizons,
        policy_id=signal.policy_id,
        policy_version=signal.policy_version,
        current_st=signal.swing_trade_maturity,
        peak_st=peak,
    )


def _countertrend_reference(
    signal: EntrySignal,
    previous: EntryOpportunitySignalReference | None,
) -> EntryOpportunitySignalReference:
    peak = signal.countertrend_maturity
    if (
        previous is not None
        and previous.peak_ct is not None
        and (peak is None or _ct_rank(previous.peak_ct) > _ct_rank(peak))
    ):
        peak = previous.peak_ct
    return EntryOpportunitySignalReference(
        signal_id=signal.signal_id,
        family=signal.family,
        setup_id=signal.setup_id,
        created_at=signal.created_at,
        entry_price=signal.entry_price,
        horizons=signal.horizons,
        policy_id=signal.policy_id,
        policy_version=signal.policy_version,
        current_ct=signal.countertrend_maturity,
        peak_ct=peak,
    )


def _swing_trade_checkpoint(
    signal: EntrySignal,
    stage: SwingTradeMaturity,
    checkpoint_id: UUID,
) -> EntryMaturityCheckpoint:
    assert signal.invalidation is not None
    return EntryMaturityCheckpoint(
        checkpoint_id=checkpoint_id,
        level=EntryMaturityLevel.ARMED,
        swing_trade_maturity=stage,
        signal_family=EntrySignalFamily.SWING_TRADE,
        setup_id=signal.setup_id,
        reached_at=signal.created_at,
        entry_price=signal.entry_price,
        current_price=signal.entry_price,
        highest_price=signal.entry_price,
        lowest_price=signal.entry_price,
        invalidation=signal.invalidation,
        target=_first_actionable_target(signal),
        zone_low=signal.zone_low,
        zone_high=signal.zone_high,
    )


def _countertrend_checkpoint(
    signal: EntrySignal,
    stage: GeriCountertrendMaturity,
    checkpoint_id: UUID,
) -> EntryMaturityCheckpoint:
    assert signal.invalidation is not None
    return EntryMaturityCheckpoint(
        checkpoint_id=checkpoint_id,
        level=EntryMaturityLevel.ARMED,
        countertrend_maturity=stage,
        signal_family=EntrySignalFamily.GERI_COUNTERTREND,
        setup_id=signal.setup_id,
        reached_at=signal.created_at,
        entry_price=signal.entry_price,
        current_price=signal.entry_price,
        highest_price=signal.entry_price,
        lowest_price=signal.entry_price,
        invalidation=signal.invalidation,
        target=_first_actionable_target(signal),
        zone_low=signal.zone_low,
        zone_high=signal.zone_high,
    )


def _st_rank(stage: SwingTradeMaturity) -> int:
    return {
        SwingTradeMaturity.ST1: 1,
        SwingTradeMaturity.ST2: 2,
        SwingTradeMaturity.ST3: 3,
        SwingTradeMaturity.ST4: 4,
    }[stage]


def _st_progress(stage: SwingTradeMaturity) -> Decimal:
    return Decimal(_st_rank(stage) * 25)


def _ct_rank(stage: GeriCountertrendMaturity) -> int:
    return {
        GeriCountertrendMaturity.CT0: 0,
        GeriCountertrendMaturity.CT1: 1,
        GeriCountertrendMaturity.CT2: 2,
        GeriCountertrendMaturity.CT3: 3,
        GeriCountertrendMaturity.CT4: 4,
    }[stage]


def _ct_progress(stage: GeriCountertrendMaturity) -> Decimal:
    return Decimal((_ct_rank(stage) + 1) * 20)


def _countertrend_paper_open(opportunity: EntryOpportunity, *, setup_id: str) -> bool:
    if opportunity.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
        return any(leg.status is EntryLegStatus.OPEN for leg in opportunity.legs)
    return any(
        item.signal_family is EntrySignalFamily.GERI_COUNTERTREND
        and item.setup_id == setup_id
        and item.countertrend_maturity is not None
        and _ct_rank(item.countertrend_maturity) >= _ct_rank(GeriCountertrendMaturity.CT1)
        and item.status is EntryCheckpointStatus.OPEN
        for item in opportunity.checkpoints
    )


def _any_countertrend_paper_open(opportunity: EntryOpportunity) -> bool:
    if opportunity.primary_signal_family is EntrySignalFamily.GERI_COUNTERTREND:
        return any(leg.status is EntryLegStatus.OPEN for leg in opportunity.legs)
    return any(
        item.signal_family is EntrySignalFamily.GERI_COUNTERTREND
        and item.countertrend_maturity is not None
        and _ct_rank(item.countertrend_maturity) >= _ct_rank(GeriCountertrendMaturity.CT1)
        and item.status is EntryCheckpointStatus.OPEN
        for item in opportunity.checkpoints
    )


def _open_countertrend_leg(
    opportunity: EntryOpportunity, signal: EntrySignal
) -> tuple[EntryHorizonLeg, ...]:
    assert signal.invalidation is not None
    target = _first_actionable_target(signal)
    output: list[EntryHorizonLeg] = []
    found = False
    for leg in opportunity.legs:
        if leg.horizon is AnalysisHorizon.SWING and leg.status is EntryLegStatus.WATCHING:
            output.append(
                leg.model_copy(
                    update={
                        "status": EntryLegStatus.OPEN,
                        "opened_at": signal.created_at,
                        "entry_price": signal.entry_price,
                        "current_price": signal.entry_price,
                        "invalidation": signal.invalidation,
                        "target": target,
                        "highest_price": signal.entry_price,
                        "lowest_price": signal.entry_price,
                    }
                )
            )
            found = True
        else:
            output.append(leg)
    if not found:
        output.append(
            EntryHorizonLeg(
                horizon=AnalysisHorizon.SWING,
                status=EntryLegStatus.OPEN,
                opened_at=signal.created_at,
                entry_price=signal.entry_price,
                current_price=signal.entry_price,
                invalidation=signal.invalidation,
                target=target,
                highest_price=signal.entry_price,
                lowest_price=signal.entry_price,
            )
        )
    return tuple(output)


def _countertrend_terminal(
    signal: EntrySignal,
) -> tuple[Decimal, EntryCloseReason, EntryLegStatus, str] | None:
    if "countertrend_expired" in signal.reasons:
        return (
            signal.entry_price,
            EntryCloseReason.EXPIRED,
            EntryLegStatus.EXPIRED,
            "geri_countertrend_expired",
        )
    if "countertrend_invalidated" in signal.reasons and signal.invalidation is not None:
        return (
            signal.invalidation,
            EntryCloseReason.ORIGINAL_THESIS_INVALIDATED,
            EntryLegStatus.INVALIDATED,
            "geri_countertrend_invalidated",
        )
    target = min(signal.targets, default=None)
    if "countertrend_target_reached" in signal.reasons and target is not None:
        return (
            target,
            EntryCloseReason.ALL_HORIZONS_CLOSED,
            EntryLegStatus.TARGET_HIT,
            "geri_countertrend_target_reached",
        )
    return None


def _add_weekdays(value: datetime, sessions: int) -> datetime:
    result = value
    remaining = sessions
    while remaining:
        result += timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def _signal_reference_for_setup(
    opportunity: EntryOpportunity, signal: EntrySignal
) -> EntryOpportunitySignalReference | None:
    return next(
        (
            item
            for item in opportunity.signal_references
            if item.family is signal.family and item.setup_id == signal.setup_id
        ),
        None,
    )


def _swing_trade_leg_matches_setup(
    opportunity: EntryOpportunity,
    leg: EntryHorizonLeg,
    *,
    setup_id: str,
) -> bool:
    if leg.horizon is not AnalysisHorizon.SWING:
        return False
    if leg.setup_id is not None:
        return leg.setup_id == setup_id
    legacy_setups = tuple(
        item.setup_id
        for item in opportunity.signal_references
        if item.family is EntrySignalFamily.SWING_TRADE
    )
    return len(legacy_setups) == 1 and legacy_setups[0] == setup_id


def _label_legacy_swing_trade_leg(
    opportunity: EntryOpportunity,
    legs: tuple[EntryHorizonLeg, ...],
) -> tuple[EntryHorizonLeg, ...]:
    unscoped = tuple(
        item
        for item in legs
        if item.horizon is AnalysisHorizon.SWING and item.setup_id is None
    )
    scoped_setup_ids = {item.setup_id for item in legs if item.setup_id is not None}
    unmatched_setups = tuple(
        item.setup_id
        for item in opportunity.signal_references
        if item.family is EntrySignalFamily.SWING_TRADE
        and item.setup_id not in scoped_setup_ids
    )
    if len(unscoped) != 1 or len(unmatched_setups) != 1:
        return legs
    legacy_leg_id = unscoped[0].leg_id
    return tuple(
        item.model_copy(update={"setup_id": unmatched_setups[0]})
        if item.leg_id == legacy_leg_id
        else item
        for item in legs
    )


def _swing_trade_setup_entered(opportunity: EntryOpportunity, *, setup_id: str) -> bool:
    return any(
        leg.opened_at is not None
        and _swing_trade_leg_matches_setup(opportunity, leg, setup_id=setup_id)
        for leg in opportunity.legs
    )


def _signal_advances_setup(existing: EntryOpportunitySignalReference, signal: EntrySignal) -> bool:
    if existing.signal_id == signal.signal_id or not _is_core_signal(signal):
        return False
    if existing.maturity is None or signal.maturity is None:
        return False
    return _rank(signal.maturity) > _rank(existing.maturity)


def _replace_signal_reference(
    current: tuple[EntryOpportunitySignalReference, ...],
    reference: EntryOpportunitySignalReference,
) -> tuple[EntryOpportunitySignalReference, ...]:
    values = [
        item
        for item in current
        if not (item.family is reference.family and item.setup_id == reference.setup_id)
    ]
    values.append(reference)
    if len(values) <= 32:
        return tuple(values)
    return (values[0], *values[-31:])


def _signal_source_ids(signal: EntrySignal) -> tuple[UUID, ...]:
    return _bounded_source_analysis_ids(
        (signal.signal_id,), tuple(dict.fromkeys(signal.source_event_ids))
    )


def _first_actionable_target(signal: EntrySignal) -> Decimal | None:
    return min(
        (value for value in signal.targets if value > signal.entry_price),
        default=None,
    )


def _signal_lifetime(signal: EntrySignal) -> timedelta:
    if AnalysisHorizon.LONG_TERM in signal.horizons:
        return timedelta(days=365)
    if AnalysisHorizon.SWING in signal.horizons:
        return timedelta(days=56)
    return timedelta(days=2)


def _legacy_signal_from_alert(alert: LocalAlert) -> EntrySignal | None:
    """Temporary edge compatibility; the v2 domain consumes only EntrySignal."""

    level = _alert_maturity(alert)
    analytical = {
        AlertKind.PATREON_CAPS_BUY: EntrySignalFamily.PATREON_CAPS,
        AlertKind.LONG_PORTFOLIO_BUY: EntrySignalFamily.LONG_PORTFOLIO,
        AlertKind.PORTFOLIO_FLOW_BUY: EntrySignalFamily.PORTFOLIO_FLOW,
    }
    family = analytical.get(alert.kind)
    if level is not None and alert.kind not in analytical:
        family = EntrySignalFamily.CORE_ENTRY
    if family is None:
        return None
    price = _alert_price(alert)
    if price is None:
        return None
    invalidation = next(
        (
            value
            for horizon in alert.horizons
            if (
                value := _alert_horizon_level(
                    alert,
                    horizon,
                    ("invalidation", "invalidation_level", "structural_invalidation"),
                )
            )
            is not None
        ),
        None,
    )
    zone_low = next(
        (
            value
            for name in ("buy_zone_low", "entry_zone_low")
            if (value := _decimal(next((m.value for m in alert.metrics if m.name == name), None)))
            is not None
        ),
        None,
    )
    zone_high = next(
        (
            value
            for name in ("buy_zone_high", "entry_zone_high")
            if (value := _decimal(next((m.value for m in alert.metrics if m.name == name), None)))
            is not None
        ),
        None,
    )
    if invalidation is not None and (zone_low is None or zone_high is None):
        zone_low = zone_high = price
    if invalidation is None:
        zone_low = zone_high = None
    target_values: list[Decimal] = []
    for horizon in alert.horizons:
        value = _alert_horizon_level(
            alert,
            horizon,
            ("objective", "target_2r", "objective_level", "take_profit"),
        )
        if value is not None and value > price:
            target_values.append(value)
    targets = tuple(dict.fromkeys(target_values))
    return EntrySignal(
        signal_id=alert.alert_id,
        family=family,
        maturity=level if family is EntrySignalFamily.CORE_ENTRY else None,
        symbol=alert.symbol,
        created_at=alert.created_at,
        setup_id=alert.deduplication_key,
        entry_price=price,
        horizons=alert.horizons,
        zone_low=zone_low,
        zone_high=zone_high,
        invalidation=invalidation,
        targets=targets,
        policy_id=(
            "core-entry" if family is EntrySignalFamily.CORE_ENTRY else family.value.lower()
        ),
        policy_version="1.0.0",
        reasons=alert.reasons,
        source_event_ids=tuple(dict.fromkeys((alert.alert_id, *alert.component_analysis_ids))),
    )


def _is_new_source_event(
    opportunity: EntryOpportunity,
    *,
    source: str,
    occurred_at: datetime,
    event_id: UUID,
) -> bool:
    cursor = next(
        (item for item in opportunity.source_cursors if item.source == source),
        None,
    )
    if cursor is None:
        return True
    return (occurred_at, event_id.int) > (cursor.occurred_at, cursor.event_id.int)


def _with_source_cursor(
    opportunity: EntryOpportunity,
    *,
    source: str,
    occurred_at: datetime,
    event_id: UUID,
) -> EntryOpportunity:
    cursor = EntryOpportunitySourceCursor(
        source=source,
        event_id=event_id,
        occurred_at=occurred_at,
    )
    values = {item.source: item for item in opportunity.source_cursors}
    values[source] = cursor
    return opportunity.model_copy(update={"source_cursors": tuple(values.values())})


def _bounded_source_analysis_ids(
    current: tuple[UUID, ...], incoming: tuple[UUID, ...]
) -> tuple[UUID, ...]:
    values = tuple(dict.fromkeys((*current, *incoming)))
    if len(values) <= _MAX_SOURCE_ANALYSIS_IDS:
        return values
    return (values[0], *values[-(_MAX_SOURCE_ANALYSIS_IDS - 1) :])


def _all_opened_legs_terminal(legs: tuple[EntryHorizonLeg, ...]) -> bool:
    opened = tuple(item for item in legs if item.opened_at is not None)
    return bool(opened) and all(item.status in _TERMINAL_LEGS for item in opened)


def _leg_close_reasons(
    previous: tuple[EntryHorizonLeg, ...],
    current: tuple[EntryHorizonLeg, ...],
) -> list[str]:
    previous_by_id = {item.leg_id: item for item in previous}
    return [
        f"{item.horizon.value.lower()}_{item.status.value.lower()}"
        for item in current
        if item.status in _TERMINAL_LEGS
        and previous_by_id[item.leg_id].status not in _TERMINAL_LEGS
    ]


def _checkpoint_close_reasons(
    previous: tuple[EntryMaturityCheckpoint, ...],
    current: tuple[EntryMaturityCheckpoint, ...],
) -> list[str]:
    previous_by_id = {item.checkpoint_id: item for item in previous}

    def maturity(item: EntryMaturityCheckpoint) -> str:
        value = item.countertrend_maturity or item.swing_trade_maturity or item.level
        return value.value.lower()

    return [
        (
            f"{item.signal_family.value.lower()}_"
            f"{maturity(item)}_{item.outcome.value.lower()}"
        )
        for item in current
        if item.status is EntryCheckpointStatus.CLOSED
        and previous_by_id[item.checkpoint_id].status is EntryCheckpointStatus.OPEN
        and item.outcome is not None
    ]


def _replace_analysis(
    analyses: tuple[AnalysisResult, ...], result: AnalysisResult
) -> tuple[AnalysisResult, ...]:
    values = {item.horizon: item for item in analyses}
    existing = values.get(result.horizon)
    if existing is None or result.as_of >= existing.as_of:
        values[result.horizon] = result
    return tuple(values[horizon] for horizon in AnalysisHorizon if horizon in values)


def _metric_decimal(result: AnalysisResult, name: str) -> Decimal | None:
    value = next((item.value for item in result.metrics if item.name == name), None)
    return _decimal(value)


def _metric_value(result: AnalysisResult, name: str) -> object:
    return next((item.value for item in result.metrics if item.name == name), None)


def _l2_anchor(opportunity: EntryOpportunity) -> EntryMaturityCheckpoint | None:
    return next(
        (
            item
            for item in reversed(opportunity.checkpoints)
            if item.level is EntryMaturityLevel.L2
            and item.signal_family is EntrySignalFamily.CORE_ENTRY
            and item.zone_low is not None
            and item.zone_high is not None
        ),
        None,
    )


def _record_l2_retest(
    opportunity: EntryOpportunity,
    *,
    result: AnalysisResult,
    price: Decimal,
    now: datetime,
) -> tuple[EntryOpportunity, bool]:
    anchor = _l2_anchor(opportunity)
    if (
        anchor is None
        or anchor.status is not EntryCheckpointStatus.OPEN
        or anchor.retested_at is not None
        or _rank(opportunity.peak_maturity) >= _rank(EntryMaturityLevel.L4)
        or result.as_of <= anchor.reached_at
    ):
        return opportunity, anchor is not None and anchor.retested_at is not None
    assert anchor.zone_low is not None and anchor.zone_high is not None
    atr = _metric_decimal(result, "atr14") or Decimal("0")
    tolerance = atr * _L2_RETEST_ATR_TOLERANCE
    touched = (
        price > anchor.invalidation
        and anchor.zone_low - tolerance <= price <= anchor.zone_high + tolerance
    )
    if not touched:
        return opportunity, False
    return _set_l2_retest(opportunity, anchor=anchor, at=now, low=price), True


def _record_l2_bar_retest(opportunity: EntryOpportunity, bar: MarketBar) -> EntryOpportunity:
    anchor = _l2_anchor(opportunity)
    if (
        anchor is None
        or anchor.status is not EntryCheckpointStatus.OPEN
        or anchor.retested_at is not None
        or bar.timestamp <= anchor.reached_at
        or _rank(opportunity.peak_maturity) >= _rank(EntryMaturityLevel.L4)
    ):
        return opportunity
    assert anchor.zone_low is not None and anchor.zone_high is not None
    if bar.low <= anchor.invalidation or not (
        bar.low <= anchor.zone_high and bar.high >= anchor.zone_low
    ):
        return opportunity
    return _set_l2_retest(
        opportunity,
        anchor=anchor,
        at=bar.timestamp,
        low=max(bar.low, anchor.zone_low),
    )


def _set_l2_retest(
    opportunity: EntryOpportunity,
    *,
    anchor: EntryMaturityCheckpoint,
    at: datetime,
    low: Decimal,
) -> EntryOpportunity:
    checkpoints = tuple(
        item.model_copy(update={"retested_at": at, "retest_low": low})
        if item.checkpoint_id == anchor.checkpoint_id
        else item
        for item in opportunity.checkpoints
    )
    return opportunity.model_copy(update={"checkpoints": checkpoints})


def _l2_reclaim_confirmed(
    opportunity: EntryOpportunity,
    *,
    result: AnalysisResult,
    price: Decimal,
) -> bool:
    anchor = _l2_anchor(opportunity)
    if (
        anchor is None
        or anchor.status is not EntryCheckpointStatus.OPEN
        or anchor.retested_at is None
        or anchor.retested_at >= result.as_of
        or price <= opportunity.invalidation
        or result.direction is not PatternDirection.BULLISH
        or result.verdict is not AnalysisVerdict.FAVORABLE
    ):
        return False
    trigger = _metric_decimal(result, "entry_trigger_level")
    if trigger is None or price < trigger:
        return False
    if not all(
        _metric_value(result, name) is True
        for name in (
            "confirmation_gate_passed",
            "mature_confirmation_gate_passed",
            "entry_efficiency_gate_passed",
            "five_minute_higher_low",
        )
    ):
        return False
    swing = next(
        (item for item in opportunity.latest_analyses if item.horizon is AnalysisHorizon.SWING),
        None,
    )
    return bool(
        swing is None
        or (
            swing.direction is PatternDirection.BULLISH
            and swing.verdict is not AnalysisVerdict.AVOID
            and _metric_value(swing, "structure_broken_confirmed") is not True
        )
    )


def _analysis_target(analyses: tuple[AnalysisResult, ...], price: Decimal) -> Decimal | None:
    by_horizon = {item.horizon: item for item in analyses}
    for horizon in (AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY):
        result = by_horizon.get(horizon)
        if result is None:
            continue
        for name in ("target_2r", "objective_level", "target"):
            value = _metric_decimal(result, name)
            if value is not None and value > price:
                return value
    return None


def _alert_zone(
    alert: LocalAlert,
    *,
    price: Decimal,
    invalidation: Decimal,
) -> tuple[Decimal, Decimal]:
    values = {item.name: item.value for item in alert.metrics}
    low = _decimal(values.get("buy_zone_low") or values.get("entry_zone_low"))
    high = _decimal(values.get("buy_zone_high") or values.get("entry_zone_high"))
    if low is None or high is None or not invalidation < low <= high:
        return price, price
    return low, high


def _alert_maturity(alert: LocalAlert) -> EntryMaturityLevel | None:
    horizons = set(alert.horizons)
    tactical = {AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY}
    swing = {AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY}
    conviction = {
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }
    if alert.kind is AlertKind.ENTRY_CONFIRMED:
        if conviction.issubset(horizons):
            return EntryMaturityLevel.L3
        if swing.issubset(horizons):
            return EntryMaturityLevel.L2
        if tactical.issubset(horizons):
            return EntryMaturityLevel.L1
    if alert.kind is AlertKind.HIGH_CONVICTION_BUY and conviction.issubset(horizons):
        return EntryMaturityLevel.L3
    if alert.kind in {AlertKind.LONG_PORTFOLIO_BUY, AlertKind.PATREON_CAPS_BUY}:
        return EntryMaturityLevel.L4
    if (
        alert.kind is AlertKind.ENTRY_WATCH
        and "ENTRY TRIGGERED" in alert.title.upper()
        and conviction.issubset(horizons)
    ):
        return EntryMaturityLevel.L4
    return None


def _alert_price(alert: LocalAlert) -> Decimal | None:
    for name in ("current_price", "reference_price"):
        value = next((item.value for item in alert.metrics if item.name == name), None)
        parsed = _decimal(value)
        if parsed is not None:
            return parsed
    by_horizon = {item.horizon: item for item in alert.component_analyses}
    for horizon in (
        AnalysisHorizon.INTRADAY,
        AnalysisHorizon.SWING,
        AnalysisHorizon.LONG_TERM,
    ):
        analysis = by_horizon.get(horizon)
        if analysis is not None:
            parsed = _metric_decimal(analysis, "reference_price")
            if parsed is not None:
                return parsed
    return None


def _alert_horizon_level(
    alert: LocalAlert,
    horizon: AnalysisHorizon,
    names: tuple[str, ...],
) -> Decimal | None:
    analysis = next(
        (item for item in alert.component_analyses if item.horizon is horizon),
        None,
    )
    sources = (
        *(analysis.metrics if analysis is not None else ()),
        *alert.metrics,
    )
    for name in names:
        parsed = _decimal(next((item.value for item in sources if item.name == name), None))
        if parsed is not None:
            return parsed
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except ValueError, ArithmeticError:
        return None
    return parsed if parsed > 0 else None


def _mark_checkpoint(
    checkpoint: EntryMaturityCheckpoint, bar: MarketBar
) -> EntryMaturityCheckpoint:
    if checkpoint.status is EntryCheckpointStatus.CLOSED or bar.timestamp <= checkpoint.reached_at:
        return checkpoint
    high = max(checkpoint.highest_price, bar.high)
    low = min(checkpoint.lowest_price, bar.low)
    updates: dict[str, object] = {
        "current_price": bar.close,
        "highest_price": high,
        "lowest_price": low,
        "mfe_percent": _percent(high, checkpoint.entry_price),
        "mae_percent": _percent(low, checkpoint.entry_price),
    }
    elapsed = bar.timestamp - checkpoint.reached_at
    for minutes, field in ((15, "return_15m"), (30, "return_30m"), (60, "return_60m")):
        if elapsed >= timedelta(minutes=minutes) and getattr(checkpoint, field) is None:
            updates[field] = _percent(bar.close, checkpoint.entry_price)
    if _is_regular_close(bar.timestamp) and checkpoint.return_close is None:
        updates["return_close"] = _percent(bar.close, checkpoint.entry_price)
    marked = checkpoint.model_copy(update=updates)
    if bar.low <= checkpoint.invalidation:
        return _close_checkpoint(
            marked,
            price=checkpoint.invalidation,
            now=bar.timestamp,
            outcome=EntryLegStatus.INVALIDATED,
        )
    if checkpoint.target is not None and bar.high >= checkpoint.target:
        return _close_checkpoint(
            marked,
            price=checkpoint.target,
            now=bar.timestamp,
            outcome=EntryLegStatus.TARGET_HIT,
        )
    return marked


def _mark_leg(leg: EntryHorizonLeg, bar: MarketBar) -> EntryHorizonLeg:
    if (
        leg.status is not EntryLegStatus.OPEN
        or leg.opened_at is None
        or bar.timestamp <= leg.opened_at
    ):
        return leg
    assert leg.entry_price is not None
    high = max(leg.highest_price, bar.high)
    low = min(leg.lowest_price, bar.low)
    marked = leg.model_copy(
        update={
            "current_price": bar.close,
            "highest_price": high,
            "lowest_price": low,
            "mfe_percent": _percent(high, leg.entry_price),
            "mae_percent": _percent(low, leg.entry_price),
        }
    )
    if bar.low <= leg.invalidation:
        return _close_leg(
            marked,
            price=leg.invalidation,
            now=bar.timestamp,
            status=EntryLegStatus.INVALIDATED,
        )
    if leg.target is not None and bar.high >= leg.target:
        return _close_leg(
            marked,
            price=leg.target,
            now=bar.timestamp,
            status=EntryLegStatus.TARGET_HIT,
        )
    return marked


def _close_checkpoint(
    checkpoint: EntryMaturityCheckpoint,
    *,
    price: Decimal,
    now: datetime,
    outcome: EntryLegStatus,
) -> EntryMaturityCheckpoint:
    if checkpoint.status is EntryCheckpointStatus.CLOSED:
        return checkpoint
    return checkpoint.model_copy(
        update={
            "status": EntryCheckpointStatus.CLOSED,
            "current_price": price,
            "closed_at": now,
            "exit_price": price,
            "outcome": outcome,
            "gain_loss_percent": _percent(price, checkpoint.entry_price),
            "highest_price": max(checkpoint.highest_price, price),
            "lowest_price": min(checkpoint.lowest_price, price),
            "mfe_percent": _percent(max(checkpoint.highest_price, price), checkpoint.entry_price),
            "mae_percent": _percent(min(checkpoint.lowest_price, price), checkpoint.entry_price),
        }
    )


def _close_leg(
    leg: EntryHorizonLeg,
    *,
    price: Decimal,
    now: datetime,
    status: EntryLegStatus,
) -> EntryHorizonLeg:
    if leg.status in _TERMINAL_LEGS or leg.status is EntryLegStatus.WATCHING:
        return leg
    assert leg.entry_price is not None
    return leg.model_copy(
        update={
            "status": status,
            "current_price": price,
            "closed_at": now,
            "exit_price": price,
            "gain_loss_percent": _percent(price, leg.entry_price),
            "highest_price": max(leg.highest_price, price),
            "lowest_price": min(leg.lowest_price, price),
            "mfe_percent": _percent(max(leg.highest_price, price), leg.entry_price),
            "mae_percent": _percent(min(leg.lowest_price, price), leg.entry_price),
        }
    )


def _rank(level: EntryMaturityLevel) -> int:
    return tuple(EntryMaturityLevel).index(level)


def _is_regular_close(value: datetime) -> bool:
    local = value.astimezone(_NEW_YORK)
    return local.time() >= time(15, 59)


def _percent(price: Decimal, entry: Decimal) -> Decimal:
    return ((price / entry - Decimal("1")) * Decimal("100")).quantize(
        _FOUR_PLACES,
        rounding=ROUND_HALF_UP,
    )
