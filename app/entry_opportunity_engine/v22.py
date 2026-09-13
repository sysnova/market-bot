# pyright: reportPrivateUsage=false
"""Extend close-confirmed profit protection to SwingTrade ST3-ST4 paper buys."""

from app.contracts import (
    EntryCheckpointStatus,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryOpportunity,
    EntrySignalFamily,
    MarketBar,
    SwingTradeMaturity,
    TradeSide,
)

from .engine import _close_checkpoint
from .v18 import _long_exit, _same_entry
from .v19 import _advance_protection
from .v21 import EntryOpportunityEngineV21


class EntryOpportunityEngineV22(EntryOpportunityEngineV21):
    engine_version = "22.0.0"

    @staticmethod
    def _mark_checkpoint(
        checkpoint: EntryMaturityCheckpoint, bar: MarketBar
    ) -> EntryMaturityCheckpoint:
        if not _eligible(checkpoint):
            return EntryOpportunityEngineV21._mark_checkpoint(checkpoint, bar)
        if (
            checkpoint.status is EntryCheckpointStatus.CLOSED
            or bar.timestamp <= checkpoint.reached_at
            or (
                checkpoint.protection_updated_at is not None
                and bar.timestamp <= checkpoint.protection_updated_at
            )
        ):
            return checkpoint
        if checkpoint.protection_stop is not None:
            exit_mark = _long_exit(bar, checkpoint.protection_stop, checkpoint.target)
            if exit_mark is not None:
                price, outcome = exit_mark
                return _close_checkpoint(
                    checkpoint.model_copy(
                        update={
                            "highest_price": max(checkpoint.highest_price, bar.open),
                            "lowest_price": min(checkpoint.lowest_price, bar.open),
                        }
                    ),
                    price=price,
                    now=bar.timestamp,
                    outcome=(
                        EntryLegStatus.PROTECTION_EXIT
                        if outcome is EntryLegStatus.INVALIDATED
                        else outcome
                    ),
                )
        marked = EntryOpportunityEngineV21._mark_checkpoint(checkpoint, bar)
        if marked.status is EntryCheckpointStatus.CLOSED:
            return marked
        return _advance_protection(marked, bar)

    def _mark_legs_for_bar(
        self, opportunity: EntryOpportunity, bar: MarketBar
    ) -> tuple[EntryHorizonLeg, ...]:
        previous_policy = super()._mark_legs_for_bar(opportunity, bar)
        # Protect only the horizon belonging to this exact ST entry. A later
        # maturity has its own entry/stop/target and cannot reprice an older leg.
        return tuple(
            self._mark_protected_leg(original, bar)
            if any(
                _eligible(cp) and _same_entry(opportunity, cp, original)
                for cp in opportunity.checkpoints
            )
            else marked
            for original, marked in zip(opportunity.legs, previous_policy, strict=True)
        )


def _eligible(cp: EntryMaturityCheckpoint) -> bool:
    return (
        cp.trade_side is TradeSide.LONG
        and cp.signal_family is EntrySignalFamily.SWING_TRADE
        and cp.swing_trade_maturity
        in {
            SwingTradeMaturity.ST3,
            SwingTradeMaturity.ST4,
        }
        and cp.entry_price > cp.invalidation
    )
