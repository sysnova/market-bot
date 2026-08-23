"""Stable structural identity for SwingTrade theses."""

from __future__ import annotations

from app.contracts import NamedValue, SwingTradeAssessment

from .models import SwingTradeContext
from .v13 import SwingTradeEngineV13


class SwingTradeEngineV14(SwingTradeEngineV13):
    """Keep strategy version as provenance, not as part of setup identity."""

    engine_version = "1.4.0"

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        result = super().analyze(context)
        setup_id = (
            f"swing-trade:{result.symbol}:{result.impulse_low_at.isoformat()}:"
            f"{result.impulse_high_at.isoformat()}"
        )
        metrics = tuple(item for item in result.metrics if item.name != "setup_id")
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "metrics": (*metrics, NamedValue(name="setup_id", value=setup_id)),
            }
        )
