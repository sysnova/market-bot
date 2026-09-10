# pyright: reportPrivateUsage=false
"""Persist human SHORT confirmations as intraday simulated trades."""

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.common.market_session import is_regular_session, is_regular_session_close_minute
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
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
    EntryOpportunityStatus,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchTransition,
    LocalAlert,
    MarketBar,
    TradeSide,
)

from .ports import EntryOpportunityStore
from .v13 import EntryOpportunityEngineV13


class EntryOpportunityEngineV14(EntryOpportunityEngineV13):
    engine_version = "14.0.0"

    def __init__(
        self,
        *,
        store: EntryOpportunityStore,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(store=store)
        self._now = now

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        if alert.kind is not AlertKind.BEARISH_CONSENSUS or (
            "short_entry_confirmed" not in alert.reasons
        ):
            if await self._short_active(alert.symbol):
                return ()
            return await super().ingest_alert(alert)
        now = self._now()
        if (
            alert.expires_at is None
            or not alert.created_at <= now < alert.expires_at
            or not is_regular_session(alert.created_at)
            or await self._store.event_seen(alert.alert_id)
        ):
            return ()
        # A replayed closing-bar confirmation must not create a new paper entry.
        intraday = next(
            (a for a in alert.component_analyses if a.horizon is AnalysisHorizon.INTRADAY), None
        )
        if intraday is not None and not (
            timedelta(0) <= now - intraday.as_of <= timedelta(minutes=2)
        ):
            return ()
        m = {x.name: x.value for x in alert.metrics}
        try:
            entry, stop, target = (
                Decimal(str(m[k]))
                for k in ("short_entry_price", "short_invalidation", "short_target")
            )
        except KeyError, ValueError, ArithmeticError:
            return ()
        if not all(x.is_finite() for x in (entry, stop, target)) or not stop > entry > target > 0:
            return ()
        active = await self._store.load_active(alert.symbol)
        if active is not None:
            # Preserve the single active thesis. Never replace an existing fill or stop.
            if active.trade_side is TradeSide.SHORT:
                return ()
            observed = active.model_copy(update={"revision": active.revision + 1})
            event = self._event(
                observed,
                occurred_at=max(alert.created_at, active.updated_at),
                reasons=("short_confirmation_conflicts_with_active_long",),
                event_id=alert.alert_id,
            )
            await self._store.save(observed, event)
            return (event,)
        latest = await self._store.load_latest(alert.symbol)
        if latest is not None and alert.created_at <= latest.updated_at:
            return ()
        if (
            latest is not None
            and intraday is not None
            and any(a.analysis_id == intraday.analysis_id for a in latest.latest_analyses)
        ):
            return ()
        at = alert.created_at
        # These alert levels are tactical Intraday levels, not a multi-day Swing stop.
        expiry = (
            at.astimezone(ZoneInfo("America/New_York"))
            .replace(hour=16, minute=0, second=0, microsecond=0)
            .astimezone(UTC)
        )
        setup = str(m.get("short_setup_id", alert.deduplication_key))
        checkpoint = EntryMaturityCheckpoint(
            trade_side=TradeSide.SHORT,
            level=EntryMaturityLevel.ARMED,
            signal_family=EntrySignalFamily.CORE_SHORT,
            setup_id=setup,
            reached_at=at,
            entry_price=entry,
            current_price=entry,
            highest_price=entry,
            lowest_price=entry,
            invalidation=stop,
            target=target,
            zone_low=entry,
            zone_high=entry,
            gain_loss_percent=Decimal(0),
        )
        leg = EntryHorizonLeg(
            trade_side=TradeSide.SHORT,
            horizon=AnalysisHorizon.INTRADAY,
            signal_family=EntrySignalFamily.CORE_SHORT,
            setup_id=setup,
            status=EntryLegStatus.OPEN,
            opened_at=at,
            expires_at=expiry,
            entry_price=entry,
            current_price=entry,
            highest_price=entry,
            lowest_price=entry,
            invalidation=stop,
            target=target,
            gain_loss_percent=Decimal(0),
        )
        opportunity = EntryOpportunity(
            trade_side=TradeSide.SHORT,
            symbol=alert.symbol,
            status=EntryOpportunityStatus.OPEN,
            current_maturity=EntryMaturityLevel.ARMED,
            peak_maturity=EntryMaturityLevel.ARMED,
            progress_percent=Decimal(100),
            armed_at=at,
            updated_at=at,
            expires_at=expiry,
            zone_low=entry,
            zone_high=entry,
            invalidation=stop,
            original_price=entry,
            current_price=entry,
            source_analysis_ids=(alert.alert_id,),
            primary_signal_family=EntrySignalFamily.CORE_SHORT,
            signal_references=(
                EntryOpportunitySignalReference(
                    signal_id=alert.alert_id,
                    family=EntrySignalFamily.CORE_SHORT,
                    setup_id=setup,
                    created_at=at,
                    entry_price=entry,
                    horizons=(AnalysisHorizon.INTRADAY,),
                    policy_id="core-short",
                    policy_version=str(m.get("short_confirmation_rule_version", "1.3.0")),
                ),
            ),
            checkpoints=(checkpoint,),
            legs=(leg,),
            latest_analyses=alert.component_analyses,
        )
        event = self._event(
            opportunity,
            occurred_at=at,
            event_id=alert.alert_id,
            reasons=("short_paper_opened", "short_entry_confirmed"),
        )
        await self._store.save(opportunity, event)
        return (event,)

    async def _short_active(self, symbol: str) -> bool:
        active = await self._store.load_active(symbol)
        return active is not None and active.trade_side is TradeSide.SHORT

    async def ingest_analysis(
        self, result: AnalysisResult, *, now: datetime
    ) -> tuple[EntryOpportunityEvent, ...]:
        if await self._short_active(result.symbol):
            return ()
        return await super().ingest_analysis(result, now=now)

    async def ingest_signal(self, signal: EntrySignal) -> tuple[EntryOpportunityEvent, ...]:
        if await self._short_active(signal.symbol):
            return ()
        return await super().ingest_signal(signal)

    async def ingest_transition(
        self, transition: EntryWatchTransition
    ) -> tuple[EntryOpportunityEvent, ...]:
        if await self._short_active(transition.symbol):
            return ()
        return await super().ingest_transition(transition)

    async def ingest_bar(self, bar: MarketBar) -> tuple[EntryOpportunityEvent, ...]:
        active = await self._store.load_active(bar.symbol)
        if active is None or active.trade_side is not TradeSide.SHORT:
            return await super().ingest_bar(bar)
        if (
            not bar.is_final
            or bar.timeframe is not BarTimeframe.MINUTE_1
            or not is_regular_session(bar.timestamp)
            or bar.timestamp < active.armed_at
            or (
                active.last_market_bar_at is not None and bar.timestamp <= active.last_market_bar_at
            )
        ):
            return ()
        leg = active.legs[0]
        assert leg.target is not None and leg.entry_price is not None
        price, outcome = bar.close, None
        # Stop first if OHLC cannot resolve the order of both touches; gaps fill at open.
        if bar.open >= leg.invalidation:
            price, outcome = bar.open, EntryLegStatus.INVALIDATED
        elif bar.open <= leg.target:
            price, outcome = bar.open, EntryLegStatus.TARGET_HIT
        elif bar.high >= leg.invalidation:
            price, outcome = leg.invalidation, EntryLegStatus.INVALIDATED
        elif bar.low <= leg.target:
            price, outcome = leg.target, EntryLegStatus.TARGET_HIT
        elif is_regular_session_close_minute(bar.timestamp):
            outcome = EntryLegStatus.SESSION_CLOSED
        # Do not attribute post-exit candle excursions to a closed trade.
        low = min(leg.lowest_price, price if outcome else bar.low)
        high = max(leg.highest_price, price if outcome else bar.high)
        updates = {
            "current_price": price,
            "lowest_price": low,
            "highest_price": high,
            "gain_loss_percent": _pnl(leg.entry_price, price),
            "mfe_percent": _pnl(leg.entry_price, low),
            "mae_percent": _pnl(leg.entry_price, high),
        }
        checkpoint = active.checkpoints[0].model_copy(update=updates)
        leg = leg.model_copy(update=updates)
        updated = active.model_copy(
            update={
                "current_price": price,
                "updated_at": bar.timestamp,
                "last_market_bar_at": bar.timestamp,
                "revision": active.revision + 1,
                "legs": (leg,),
                "checkpoints": (checkpoint,),
            }
        )
        reasons = ("short_paper_marked",)
        if outcome:
            updated = self._close_opportunity(
                updated,
                price=price,
                now=bar.timestamp,
                reason=EntryCloseReason.ALL_HORIZONS_CLOSED,
                leg_status=outcome,
            )
            reasons = ("short_paper_closed", outcome.value.lower())
        event = self._event(updated, occurred_at=bar.timestamp, reasons=reasons)
        await self._store.save(updated, event)
        return (event,)

    def _close_opportunity(
        self,
        opportunity: EntryOpportunity,
        *,
        price: Decimal,
        now: datetime,
        reason: EntryCloseReason,
        leg_status: EntryLegStatus,
    ) -> EntryOpportunity:
        if opportunity.trade_side is not TradeSide.SHORT:
            return super()._close_opportunity(
                opportunity, price=price, now=now, reason=reason, leg_status=leg_status
            )
        updates = {
            "closed_at": now,
            "exit_price": price,
            "current_price": price,
            "gain_loss_percent": _pnl(opportunity.original_price, price),
        }
        return opportunity.model_copy(
            update={
                "status": EntryOpportunityStatus.CLOSED,
                "close_reason": reason,
                "closed_at": now,
                "updated_at": max(opportunity.updated_at, now),
                "current_price": price,
                "revision": opportunity.revision + 1,
                "legs": tuple(
                    x.model_copy(update={**updates, "status": leg_status}) for x in opportunity.legs
                ),
                "checkpoints": tuple(
                    x.model_copy(
                        update={
                            **updates,
                            "status": EntryCheckpointStatus.CLOSED,
                            "outcome": leg_status,
                        }
                    )
                    for x in opportunity.checkpoints
                ),
            }
        )

    async def reconcile(
        self, *, now: datetime, active_symbols: Collection[str]
    ) -> tuple[EntryOpportunityEvent, ...]:
        # Close expired short trades first; legacy reconciliation then sees only longs.
        events: list[EntryOpportunityEvent] = []
        for active in await self._store.list_active():
            if active.trade_side is TradeSide.SHORT and now >= active.expires_at:
                closed = self._close_opportunity(
                    active,
                    price=active.current_price,
                    now=now,
                    reason=EntryCloseReason.EXPIRED,
                    leg_status=EntryLegStatus.SESSION_CLOSED,
                )
                event = self._event(
                    closed,
                    occurred_at=now,
                    reasons=("short_session_closed_at_last_observed_price",),
                )
                await self._store.save(closed, event)
                events.append(event)
        events.extend(await super().reconcile(now=now, active_symbols=active_symbols))
        return tuple(events)


def _pnl(entry: Decimal, current: Decimal) -> Decimal:
    return ((entry - current) / entry * 100).quantize(Decimal("0.0001"))
