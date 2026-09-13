# pyright: reportPrivateUsage=false
"""An Intraday L2 reclaim must retain the confirming assessment's level pair."""

from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryWatchTransition,
)

from .engine import _metric_decimal
from .v16 import EntryOpportunityEngineV16


class EntryOpportunityEngineV17(EntryOpportunityEngineV16):
    engine_version = "17.0.0"

    def _new_opportunity(
        self,
        transition: EntryWatchTransition,
        *,
        level: EntryMaturityLevel,
    ) -> EntryOpportunity:
        opportunity = super()._new_opportunity(transition, level=level)
        if (
            level not in {EntryMaturityLevel.L1, EntryMaturityLevel.L4}
            or transition.entry_invalidation is None
            or transition.entry_target is None
        ):
            return opportunity
        # A confirmation may be the first delivered event after replay/restart.
        # Seed its new checkpoint with the confirmed levels, not the original watch.
        return opportunity.model_copy(
            update={
                "checkpoints": tuple(
                    cp.model_copy(
                        update={
                            "invalidation": transition.entry_invalidation,
                            "target": transition.entry_target,
                        }
                    )
                    for cp in opportunity.checkpoints
                )
            }
        )

    def _l2_reclaim_levels(
        self,
        opportunity: EntryOpportunity,
        *,
        result: AnalysisResult,
        price: Decimal,
    ) -> tuple[Decimal, Decimal | None] | None:
        if result.horizon is not AnalysisHorizon.INTRADAY or not price.is_finite():
            return None
        stop = _metric_decimal(result, "invalidation_level")
        target = _metric_decimal(result, "objective_level")
        if (
            stop is None
            or target is None
            or not stop.is_finite()
            or not target.is_finite()
            or not 0 < stop < price < target
        ):
            return None
        return stop, target
