# pyright: reportPrivateUsage=false
"""Core long entry protection, armed by completed closes and effective next bar."""

from app.contracts import (
    EntryCheckpointStatus,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntrySignalFamily,
    MarketBar,
    TradeSide,
)

from .engine import _close_checkpoint, _close_leg
from .v18 import EntryOpportunityEngineV18, _long_exit, _same_entry


class EntryOpportunityEngineV19(EntryOpportunityEngineV18):
    engine_version = "19.0.0"

    @staticmethod
    def _bar_can_close_opportunity(
        opportunity: EntryOpportunity,
        *,
        legs: tuple[EntryHorizonLeg, ...],
        checkpoints: tuple[EntryMaturityCheckpoint, ...],
    ) -> bool:
        # Closing L1's last horizon must not liquidate a different L2-L4 entry
        # whose own stop/target/protection has not been reached.
        return not any(
            _eligible(cp) and cp.status is EntryCheckpointStatus.OPEN for cp in checkpoints
        ) and EntryOpportunityEngineV18._bar_can_close_opportunity(
            opportunity, legs=legs, checkpoints=checkpoints
        )

    @staticmethod
    def _mark_checkpoint(
        checkpoint: EntryMaturityCheckpoint, bar: MarketBar
    ) -> EntryMaturityCheckpoint:
        if not _eligible(checkpoint):
            return EntryOpportunityEngineV18._mark_checkpoint(checkpoint, bar)
        if (
            checkpoint.status is EntryCheckpointStatus.CLOSED
            or bar.timestamp <= checkpoint.reached_at
        ):
            return checkpoint
        if (
            checkpoint.protection_updated_at is not None
            and bar.timestamp <= checkpoint.protection_updated_at
        ):
            return checkpoint
        protected = checkpoint.protection_stop
        if protected is not None:
            exit_mark = _long_exit(bar, protected, checkpoint.target)
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
        marked = EntryOpportunityEngineV18._mark_checkpoint(checkpoint, bar)
        if marked.status is EntryCheckpointStatus.CLOSED:
            return marked
        return _advance_protection(marked, bar)

    def _mark_legs_for_bar(
        self, opportunity: EntryOpportunity, bar: MarketBar
    ) -> tuple[EntryHorizonLeg, ...]:
        # Only protect actual Core buys; reference checkpoints and other strategies
        # keep their own management. Legacy legs may have no explicit family.
        return tuple(
            self._mark_protected_leg(leg, bar)
            if any(
                _eligible(cp) and _same_entry(opportunity, cp, leg)
                for cp in opportunity.checkpoints
            )
            else EntryOpportunityEngineV18._mark_leg(leg, bar)
            for leg in opportunity.legs
        )

    @staticmethod
    def _mark_protected_leg(leg: EntryHorizonLeg, bar: MarketBar) -> EntryHorizonLeg:
        if (
            leg.status is not EntryLegStatus.OPEN
            or leg.opened_at is None
            or bar.timestamp <= leg.opened_at
        ):
            return leg
        if leg.protection_updated_at is not None and bar.timestamp <= leg.protection_updated_at:
            return leg
        if leg.protection_stop is not None:
            exit_mark = _long_exit(bar, leg.protection_stop, leg.target)
            if exit_mark is not None:
                price, outcome = exit_mark
                return _close_leg(
                    leg.model_copy(
                        update={
                            "highest_price": max(leg.highest_price, bar.open),
                            "lowest_price": min(leg.lowest_price, bar.open),
                        }
                    ),
                    price=price,
                    now=bar.timestamp,
                    status=(
                        EntryLegStatus.PROTECTION_EXIT
                        if outcome is EntryLegStatus.INVALIDATED
                        else outcome
                    ),
                )
        marked = EntryOpportunityEngineV18._mark_leg(leg, bar)
        if marked.status is not EntryLegStatus.OPEN:
            return marked
        return _advance_protection(marked, bar)

    @staticmethod
    def _protection_reasons(
        before: tuple[EntryMaturityCheckpoint, ...],
        after: tuple[EntryMaturityCheckpoint, ...],
    ) -> list[str]:
        previous = {cp.checkpoint_id: cp.protection_stop for cp in before}
        return (
            ["profit_protection_updated"]
            if any(cp.protection_stop != previous.get(cp.checkpoint_id) for cp in after)
            else []
        )


def _eligible(cp: EntryMaturityCheckpoint) -> bool:
    return (
        cp.trade_side is TradeSide.LONG
        and cp.signal_family in {EntrySignalFamily.CORE_ENTRY, EntrySignalFamily.CORE_RECOVERY}
        and cp.level
        in {
            EntryMaturityLevel.L1,
            EntryMaturityLevel.L2,
            EntryMaturityLevel.L3,
            EntryMaturityLevel.L4,
        }
        and cp.entry_price > cp.invalidation
    )


def _advance_protection[T: (EntryMaturityCheckpoint, EntryHorizonLeg)](
    item: T, bar: MarketBar
) -> T:
    if item.entry_price is None:
        return item
    risk = item.entry_price - item.invalidation
    gain = bar.close - item.entry_price
    if risk <= 0 or gain < risk:
        return item
    proposed = max(item.entry_price, bar.close - risk)
    if item.protection_stop is not None and proposed <= item.protection_stop:
        return item
    return item.model_copy(
        update={
            "protection_stop": proposed,
            "protection_updated_at": bar.timestamp,
            "protection_rule_version": "1.0.0",
        }
    )
