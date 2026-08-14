"""Consolidate Entry Watcher evidence into one auditable paper opportunity per ticker."""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

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
    LocalAlert,
    MarketBar,
    PatternDirection,
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
                        {
                            horizon: transition.entry_invalidation
                            for horizon in transition.horizons
                        }
                        if transition.entry_invalidation is not None
                        else None
                    ),
                    horizon_targets=(
                        {
                            horizon: transition.entry_target
                            for horizon in transition.horizons
                        }
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
            closed = self._close_opportunity(
                active,
                price=transition.current_price,
                now=transition.occurred_at,
                reason=EntryCloseReason.POLICY_INELIGIBLE,
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
                reasons=("opportunity_policy_ineligible", *transition.reasons),
                event_id=transition.transition_id,
            )
            await self._store.save(closed, event)
            return (event,)

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
                {
                    horizon: transition.entry_invalidation
                    for horizon in transition.horizons
                }
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
            changed.peak_maturity is not active.peak_maturity
            or changed.status is not active.status
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
        sources = _bounded_source_analysis_ids(
            active.source_analysis_ids, (result.analysis_id,)
        )
        updated = active.model_copy(
            update={
                "current_price": price,
                "updated_at": now,
                "revision": active.revision + 1,
                "latest_analyses": analyses,
                "source_analysis_ids": sources,
            }
        )
        original_breached = price <= active.invalidation
        bearish_failure = (
            result.direction is PatternDirection.BEARISH
            and result.verdict in {AnalysisVerdict.AVOID, AnalysisVerdict.CAUTION}
        )
        if original_breached or (
            result.horizon is AnalysisHorizon.LONG_TERM
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
        changed = self._advance(
            active,
            level=level,
            price=price,
            now=alert.created_at,
            horizons=alert.horizons,
            source_analysis_ids=alert.component_analysis_ids,
            checkpoint_target=checkpoint_target,
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

        if not bar.is_final or bar.timeframe is not BarTimeframe.MINUTE_1:
            return ()
        active = await self._store.load_active(bar.symbol)
        if active is None or bar.timestamp < active.armed_at:
            return ()
        checkpoints = tuple(_mark_checkpoint(item, bar) for item in active.checkpoints)
        legs = tuple(_mark_leg(item, bar) for item in active.legs)
        reasons = _leg_close_reasons(active.legs, legs)

        if bar.low <= active.invalidation:
            updated = active.model_copy(
                update={"checkpoints": checkpoints, "legs": legs}
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
                "updated_at": bar.timestamp,
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
        by_horizon = {item.horizon: item for item in legs}
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

    async def ingest_signal(
        self, signal: EntrySignal
    ) -> tuple[EntryOpportunityEvent, ...]:
        """Apply one decision by stable family/setup, independent of its producer."""

        if await self._store.event_seen(signal.signal_id):
            return ()
        active = await self._store.load_active(signal.symbol)
        existing = (
            _signal_reference_for_setup(active, signal) if active is not None else None
        )
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
            invalidations = (
                {horizon: signal.invalidation for horizon in signal.horizons}
                if signal.invalidation is not None
                else {}
            )
            target = _first_actionable_target(signal)
            targets = (
                {horizon: target for horizon in signal.horizons}
                if target is not None
                else {}
            )
            changed = self._advance(
                active,
                level=signal.maturity,
                price=signal.entry_price,
                now=signal.created_at,
                horizons=signal.horizons,
                source_analysis_ids=source_ids,
                checkpoint_target=target,
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

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        """Compatibility adapter; new integrations must publish EntrySignal directly."""

        signal = _legacy_signal_from_alert(alert)
        return () if signal is None else await self.ingest_signal(signal)

    def _new_signal_opportunity(self, signal: EntrySignal) -> EntryOpportunity:
        assert signal.zone_low is not None
        assert signal.zone_high is not None
        assert signal.invalidation is not None
        analytical = not _is_core_signal(signal)
        initial_level = (
            EntryMaturityLevel.ARMED if analytical else signal.maturity
        )
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
        targets = (
            {horizon: target for horizon in signal.horizons}
            if target is not None
            else {}
        )
        legs = self._open_horizons(
            (),
            horizons=signal.horizons,
            price=signal.entry_price,
            invalidation=signal.invalidation,
            now=signal.created_at,
            horizon_invalidations={
                horizon: signal.invalidation for horizon in signal.horizons
            },
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
        setup_id=signal.setup_id,
        created_at=signal.created_at,
        entry_price=signal.entry_price,
        horizons=signal.horizons,
        policy_id=signal.policy_id,
        policy_version=signal.policy_version,
    )


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


def _signal_advances_setup(
    existing: EntryOpportunitySignalReference, signal: EntrySignal
) -> bool:
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
            "core-entry"
            if family is EntrySignalFamily.CORE_ENTRY
            else family.value.lower()
        ),
        policy_version="1.0.0",
        reasons=alert.reasons,
        source_event_ids=tuple(
            dict.fromkeys((alert.alert_id, *alert.component_analysis_ids))
        ),
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
    except (ValueError, ArithmeticError):
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
