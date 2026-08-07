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
    EntryOpportunityStatus,
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


class EntryOpportunityEngine:
    """Own progression, paper legs, markouts, and closure of ticker opportunities."""

    engine_id = "entry-opportunity"
    engine_version = "1.0.0"

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
            if transition.status not in {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}:
                return ()
            level = (
                EntryMaturityLevel.IN_ZONE
                if transition.status is EntryWatchStatus.IN_ZONE
                else EntryMaturityLevel.ARMED
            )
            opportunity = self._new_opportunity(transition, level=level)
            event = self._event(
                opportunity,
                occurred_at=transition.occurred_at,
                reasons=("opportunity_created", *transition.reasons),
                event_id=transition.transition_id,
            )
            await self._store.save(opportunity, event)
            return (event,)

        if transition.occurred_at < active.updated_at:
            return ()
        if transition.status is EntryWatchStatus.INVALIDATED:
            closed = self._close_opportunity(
                active,
                price=transition.current_price,
                now=transition.occurred_at,
                reason=EntryCloseReason.ORIGINAL_THESIS_INVALIDATED,
                leg_status=EntryLegStatus.THESIS_BROKEN,
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
            event = self._event(
                closed,
                occurred_at=transition.occurred_at,
                reasons=("opportunity_expired", *transition.reasons),
                event_id=transition.transition_id,
            )
            await self._store.save(closed, event)
            return (event,)

        level = {
            EntryWatchStatus.ARMED: EntryMaturityLevel.ARMED,
            EntryWatchStatus.IN_ZONE: EntryMaturityLevel.IN_ZONE,
            EntryWatchStatus.TRIGGERED: EntryMaturityLevel.L4,
        }[transition.status]
        changed = self._advance(
            active,
            level=level,
            price=transition.current_price,
            now=transition.occurred_at,
            horizons=transition.horizons,
            source_analysis_ids=transition.source_analysis_ids,
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
        price = _metric_decimal(result, "reference_price") or active.current_price
        analyses = _replace_analysis(active.latest_analyses, result)
        sources = tuple(dict.fromkeys((*active.source_analysis_ids, result.analysis_id)))
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

        if bearish_failure and active.status is EntryOpportunityStatus.OPEN:
            changed, closed_leg = self._close_horizon(
                updated,
                horizon=result.horizon,
                price=price,
                now=now,
                status=EntryLegStatus.INVALIDATED,
            )
            if closed_leg:
                event = self._event(
                    changed,
                    occurred_at=now,
                    reasons=(f"{result.horizon.value.lower()}_invalidated",),
                    event_id=result.analysis_id,
                )
                await self._store.save(changed, event)
                return (event,)

        audit_event = self._event(
            updated,
            occurred_at=now,
            reasons=(f"{result.horizon.value.lower()}_evidence_updated",),
            event_id=result.analysis_id,
        )
        await self._store.save(updated, audit_event)
        return ()

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        """Record L1-L4 maturity without replacing the ticker's original thesis."""

        level = _alert_maturity(alert)
        if level is None or await self._store.event_seen(alert.alert_id):
            return ()
        active = await self._store.load_active(alert.symbol)
        if active is None or alert.created_at < active.updated_at:
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
        reasons: list[str] = []

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
        horizon_invalidations: dict[AnalysisHorizon, Decimal] | None = None,
        horizon_targets: dict[AnalysisHorizon, Decimal] | None = None,
    ) -> EntryOpportunity:
        current_rank = _rank(opportunity.peak_maturity)
        level_rank = _rank(level)
        peak = level if level_rank > current_rank else opportunity.peak_maturity
        checkpoints = opportunity.checkpoints
        if level_rank > current_rank and all(item.level is not level for item in checkpoints):
            checkpoints = (
                *checkpoints,
                self._new_checkpoint(
                    level,
                    price=price,
                    invalidation=opportunity.invalidation,
                    reached_at=now,
                    target=checkpoint_target,
                ),
            )
        status = opportunity.status
        legs = opportunity.legs
        if level is EntryMaturityLevel.IN_ZONE and status is EntryOpportunityStatus.ARMED:
            status = EntryOpportunityStatus.IN_ZONE
        if level in {
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
                    level if level_rank >= current_rank else opportunity.current_maturity
                ),
                "peak_maturity": peak,
                "progress_percent": _PROGRESS[peak],
                "current_price": price,
                "updated_at": now,
                "revision": opportunity.revision + 1,
                "source_analysis_ids": tuple(
                    dict.fromkeys((*opportunity.source_analysis_ids, *source_analysis_ids))
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
    ) -> EntryMaturityCheckpoint:
        return EntryMaturityCheckpoint(
            checkpoint_id=self._child_id_factory(),
            level=level,
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
                "current_price": price,
                "updated_at": now,
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
