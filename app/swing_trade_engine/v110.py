"""Keep Fibonacci context separate from a strictly valid LONG entry zone."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

from app.contracts import SwingTradeAssessment

from .models import SwingTradeContext
from .v17 import metric
from .v18 import SwingTradeEngineV18


class SwingTradeEngineV110(SwingTradeEngineV18):
    engine_version = "1.10.0"

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.engine_version in {"1.7.0", "1.8.0", "1.9.0"}
            and metric(previous, "rebound_state") in {"OPEN", "EXITED"}
        ):
            context = replace(
                context,
                previous_assessment=previous.model_copy(
                    update={"engine_version": self.engine_version}
                ),
            )
        return self.entry_geometry(super().analyze(context))

    @staticmethod
    def entry_geometry(item: SwingTradeAssessment) -> SwingTradeAssessment:
        """Never widen a stop or present the invalid portion of Fibonacci as an entry."""
        stop = item.invalidation
        low = max(item.zone_low, item.support_band_low)
        high = item.zone_high
        if metric(item, "rebound_state") in {"OPEN", "EXITED"}:
            # After acceptance the registered entry, including an exit below its stop,
            # retains its original entry price and risk instead of today's Fibonacci.
            entry = metric(item, "rebound_entry_price")
            operational_stop = metric(item, "operational_stop")
            if entry is None or operational_stop is None:
                raise ValueError("SwingTrade rebound is missing frozen entry levels")
            low = high = Decimal(str(entry))
            stop = Decimal(str(operational_stop))
        valid = stop < low <= high
        updates: dict[str, object] = dict(
            entry_zone_low=low if valid else None,
            entry_zone_high=high if valid else None,
            entry_invalidation=stop if valid else None,
        )
        if not valid:
            updates.update(
                maturity=None,
                eligible=False,
                reasons=(*item.reasons, "no_valid_entry_zone_above_invalidation"),
            )
        updates["context_hash"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps([item.context_hash, str(low), str(high), str(stop), valid]).encode()
            ).hexdigest()
        )
        return item.model_copy(update=updates)
