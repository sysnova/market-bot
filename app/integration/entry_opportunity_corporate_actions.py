"""Explicit corporate-action repairs for persisted entry opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import EntryHorizonLeg, EntryMaturityCheckpoint, EntryOpportunity
from app.contracts.entry_opportunity import EntryOpportunitySignalReference, RecoveryExitState


@dataclass(frozen=True)
class StockSplitAdjustment:
    symbol: str
    effective_at: datetime
    ratio: Decimal


APH_2026_SPLIT = StockSplitAdjustment(
    symbol="APH",
    effective_at=datetime(2026, 9, 3, tzinfo=UTC),
    ratio=Decimal("2"),
)

KNOWN_STOCK_SPLITS = (APH_2026_SPLIT,)


def apply_stock_split_adjustment(
    opportunity: EntryOpportunity,
    adjustment: StockSplitAdjustment,
) -> EntryOpportunity:
    """Rebase pre-split thesis prices into the post-split price scale."""

    if opportunity.symbol != adjustment.symbol:
        return opportunity
    if opportunity.armed_at >= adjustment.effective_at:
        return opportunity

    mark_at = opportunity.last_market_bar_at or opportunity.updated_at
    current_price = (
        _adjust_price(opportunity.current_price, adjustment)
        if mark_at < adjustment.effective_at
        else opportunity.current_price
    )
    return opportunity.model_copy(
        update={
            "zone_low": _adjust_price(opportunity.zone_low, adjustment),
            "zone_high": _adjust_price(opportunity.zone_high, adjustment),
            "invalidation": _adjust_price(opportunity.invalidation, adjustment),
            "original_price": _adjust_price(opportunity.original_price, adjustment),
            "current_price": current_price,
            "signal_references": tuple(
                _adjust_reference(item, adjustment) for item in opportunity.signal_references
            ),
            "legs": tuple(
                _adjust_leg(item, adjustment, mark_at=mark_at) for item in opportunity.legs
            ),
            "checkpoints": tuple(
                _adjust_checkpoint(item, adjustment, mark_at=mark_at)
                for item in opportunity.checkpoints
            ),
        }
    )


def _adjust_checkpoint(
    checkpoint: EntryMaturityCheckpoint,
    adjustment: StockSplitAdjustment,
    *,
    mark_at: datetime,
) -> EntryMaturityCheckpoint:
    if checkpoint.reached_at >= adjustment.effective_at:
        return checkpoint
    current_price = (
        _adjust_price(checkpoint.current_price, adjustment)
        if mark_at < adjustment.effective_at
        else checkpoint.current_price
    )
    return checkpoint.model_copy(
        update={
            "entry_price": _adjust_price(checkpoint.entry_price, adjustment),
            "current_price": current_price,
            "highest_price": _adjust_extreme(
                checkpoint.highest_price,
                adjustment,
                mark_at=mark_at,
                current_price=current_price,
            ),
            "lowest_price": _adjust_extreme(
                checkpoint.lowest_price,
                adjustment,
                mark_at=mark_at,
                current_price=current_price,
            ),
            "invalidation": _adjust_price(checkpoint.invalidation, adjustment),
            "target": _adjust_optional_price(checkpoint.target, adjustment),
            "zone_low": _adjust_optional_price(checkpoint.zone_low, adjustment),
            "zone_high": _adjust_optional_price(checkpoint.zone_high, adjustment),
            "retest_low": _adjust_optional_price(checkpoint.retest_low, adjustment),
            "exit_price": _adjust_optional_price(checkpoint.exit_price, adjustment),
            "protection_stop": _adjust_optional_price(checkpoint.protection_stop, adjustment),
            "recovery_exit": _adjust_recovery_exit(checkpoint.recovery_exit, adjustment),
        }
    )


def _adjust_leg(
    leg: EntryHorizonLeg,
    adjustment: StockSplitAdjustment,
    *,
    mark_at: datetime,
) -> EntryHorizonLeg:
    if leg.opened_at is not None and leg.opened_at >= adjustment.effective_at:
        return leg
    current_price = (
        _adjust_price(leg.current_price, adjustment)
        if mark_at < adjustment.effective_at
        else leg.current_price
    )
    return leg.model_copy(
        update={
            "entry_price": _adjust_optional_price(leg.entry_price, adjustment),
            "current_price": current_price,
            "invalidation": _adjust_price(leg.invalidation, adjustment),
            "target": _adjust_optional_price(leg.target, adjustment),
            "highest_price": _adjust_extreme(
                leg.highest_price,
                adjustment,
                mark_at=mark_at,
                current_price=current_price,
            ),
            "lowest_price": _adjust_extreme(
                leg.lowest_price,
                adjustment,
                mark_at=mark_at,
                current_price=current_price,
            ),
            "exit_price": _adjust_optional_price(leg.exit_price, adjustment),
            "protection_stop": _adjust_optional_price(leg.protection_stop, adjustment),
        }
    )


def _adjust_reference(
    reference: EntryOpportunitySignalReference,
    adjustment: StockSplitAdjustment,
) -> EntryOpportunitySignalReference:
    if reference.created_at >= adjustment.effective_at:
        return reference
    return reference.model_copy(
        update={"entry_price": _adjust_price(reference.entry_price, adjustment)}
    )


def _adjust_recovery_exit(
    recovery: RecoveryExitState | None,
    adjustment: StockSplitAdjustment,
) -> RecoveryExitState | None:
    if recovery is None or recovery.evidence_at >= adjustment.effective_at:
        return recovery
    return recovery.model_copy(
        update={
            "avwap": _adjust_price(recovery.avwap, adjustment),
            "breakout_level": _adjust_price(recovery.breakout_level, adjustment),
            "rebound_low": _adjust_price(recovery.rebound_low, adjustment),
            "reaction_low": _adjust_price(recovery.reaction_low, adjustment),
            "previous_failed_close": _adjust_optional_price(
                recovery.previous_failed_close, adjustment
            ),
        }
    )


def _adjust_optional_price(
    value: Decimal | None,
    adjustment: StockSplitAdjustment,
) -> Decimal | None:
    return None if value is None else _adjust_price(value, adjustment)


def _adjust_price(value: Decimal, adjustment: StockSplitAdjustment) -> Decimal:
    return value / adjustment.ratio


def _adjust_extreme(
    value: Decimal,
    adjustment: StockSplitAdjustment,
    *,
    mark_at: datetime,
    current_price: Decimal,
) -> Decimal:
    if mark_at < adjustment.effective_at:
        return _adjust_price(value, adjustment)
    if value > current_price * (Decimal("1") + (adjustment.ratio - Decimal("1")) / Decimal("2")):
        return _adjust_price(value, adjustment)
    return value
