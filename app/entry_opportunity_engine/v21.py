# pyright: reportPrivateUsage=false
"""Extend close-confirmed profit protection to GERI CT2-CT4 paper buys."""

from app.contracts import (
    EntryCheckpointStatus,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryOpportunity,
    EntrySignalFamily,
    GeriCountertrendMaturity,
    MarketBar,
    TradeSide,
)

from .engine import _close_checkpoint
from .v18 import _long_exit, _same_entry
from .v19 import _advance_protection
from .v20 import EntryOpportunityEngineV20


class EntryOpportunityEngineV21(EntryOpportunityEngineV20):
    engine_version = "21.0.0"

    @staticmethod
    def _mark_checkpoint(
        checkpoint: EntryMaturityCheckpoint, bar: MarketBar
    ) -> EntryMaturityCheckpoint:
        if not _eligible(checkpoint):
            return EntryOpportunityEngineV20._mark_checkpoint(checkpoint, bar)
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
        marked = EntryOpportunityEngineV20._mark_checkpoint(checkpoint, bar)
        if marked.status is EntryCheckpointStatus.CLOSED:
            return marked
        return _advance_protection(marked, bar)

    def _mark_legs_for_bar(
        self, opportunity: EntryOpportunity, bar: MarketBar
    ) -> tuple[EntryHorizonLeg, ...]:
        previous_policy = super()._mark_legs_for_bar(opportunity, bar)
        # Protect only the horizon belonging to this exact CT entry. A later
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
        and cp.signal_family is EntrySignalFamily.GERI_COUNTERTREND
        and cp.countertrend_maturity
        in {
            GeriCountertrendMaturity.CT2,
            GeriCountertrendMaturity.CT3,
            GeriCountertrendMaturity.CT4,
        }
        and cp.entry_price > cp.invalidation
    )
