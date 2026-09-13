# pyright: reportPrivateUsage=false
"""Gap-aware simulated exits and consistent closure of one entry's open legs."""

from datetime import datetime
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryOpportunity,
    EntrySignalFamily,
    MarketBar,
)

from .engine import _CORE_FAMILIES, _close_checkpoint, _close_leg, _leg_family
from .v17 import EntryOpportunityEngineV17


class EntryOpportunityEngineV18(EntryOpportunityEngineV17):
    engine_version = "18.0.0"

    @staticmethod
    def _original_stop_price(opportunity: EntryOpportunity, bar: MarketBar) -> Decimal:
        return min(opportunity.invalidation, bar.open)

    @staticmethod
    def _mark_checkpoint(
        checkpoint: EntryMaturityCheckpoint, bar: MarketBar
    ) -> EntryMaturityCheckpoint:
        if (
            checkpoint.status is EntryCheckpointStatus.CLOSED
            or bar.timestamp <= checkpoint.reached_at
        ):
            return checkpoint
        exit_mark = _long_exit(bar, checkpoint.invalidation, checkpoint.target)
        if exit_mark is None:
            return EntryOpportunityEngineV17._mark_checkpoint(checkpoint, bar)
        price, outcome = exit_mark
        # Only the open and exit are known to occur before closure; intrabar OHLC
        # cannot tell whether the other extremes preceded or followed the exit.
        observed = checkpoint.model_copy(
            update={
                "highest_price": max(checkpoint.highest_price, bar.open),
                "lowest_price": min(checkpoint.lowest_price, bar.open),
            }
        )
        return _close_checkpoint(observed, price=price, now=bar.timestamp, outcome=outcome)

    @staticmethod
    def _mark_leg(leg: EntryHorizonLeg, bar: MarketBar) -> EntryHorizonLeg:
        if (
            leg.status is not EntryLegStatus.OPEN
            or leg.opened_at is None
            or bar.timestamp <= leg.opened_at
        ):
            return leg
        exit_mark = _long_exit(bar, leg.invalidation, leg.target)
        if exit_mark is None:
            return EntryOpportunityEngineV17._mark_leg(leg, bar)
        price, outcome = exit_mark
        observed = leg.model_copy(
            update={
                "highest_price": max(leg.highest_price, bar.open),
                "lowest_price": min(leg.lowest_price, bar.open),
            }
        )
        return _close_leg(observed, price=price, now=bar.timestamp, status=outcome)

    def _close_core_entries(
        self,
        opportunity: EntryOpportunity,
        *,
        price: Decimal,
        now: datetime,
        evidence_at: datetime,
        reason: EntryCloseReason,
        leg_status: EntryLegStatus,
        horizon: AnalysisHorizon | None = None,
        price_breach_only: bool = False,
        families: frozenset[EntrySignalFamily] = _CORE_FAMILIES,
    ) -> EntryOpportunity:
        closed = super()._close_core_entries(
            opportunity,
            price=price,
            now=now,
            evidence_at=evidence_at,
            reason=reason,
            leg_status=leg_status,
            horizon=horizon,
            price_breach_only=price_breach_only,
            families=families,
        )
        previously_open = {
            cp.checkpoint_id
            for cp in opportunity.checkpoints
            if cp.status is EntryCheckpointStatus.OPEN
        }
        exits = [
            cp
            for cp in closed.checkpoints
            if cp.checkpoint_id in previously_open and cp.status is EntryCheckpointStatus.CLOSED
        ]
        legs: list[EntryHorizonLeg] = []
        for leg in closed.legs:
            matching = next((cp for cp in exits if _same_entry(closed, cp, leg)), None)
            if matching is not None and leg.status is EntryLegStatus.OPEN:
                assert matching.exit_price is not None and matching.closed_at is not None
                assert matching.outcome is not None
                leg = _close_leg(
                    leg, price=matching.exit_price, now=matching.closed_at, status=matching.outcome
                )
            legs.append(leg)
        return (
            closed.model_copy(update={"legs": tuple(legs)})
            if tuple(legs) != closed.legs
            else closed
        )


def _same_entry(
    opportunity: EntryOpportunity, cp: EntryMaturityCheckpoint, leg: EntryHorizonLeg
) -> bool:
    return (
        cp.signal_family is _leg_family(opportunity, leg)
        and leg.opened_at == cp.reached_at
        and leg.entry_price == cp.entry_price
        and leg.invalidation == cp.invalidation
        and leg.target == cp.target
        and (leg.setup_id is None or leg.setup_id == cp.setup_id)
    )


def _long_exit(
    bar: MarketBar, stop: Decimal, target: Decimal | None
) -> tuple[Decimal, EntryLegStatus] | None:
    if bar.open <= stop:
        return bar.open, EntryLegStatus.INVALIDATED
    if target is not None and bar.open >= target:
        return bar.open, EntryLegStatus.TARGET_HIT
    # Stop first when both levels occur inside a candle and their order is unknown.
    if bar.low <= stop:
        return stop, EntryLegStatus.INVALIDATED
    if target is not None and bar.high >= target:
        return target, EntryLegStatus.TARGET_HIT
    return None
