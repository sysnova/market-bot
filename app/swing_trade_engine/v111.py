"""Combine confirmed local support retests with valid, frozen entry geometry."""

from dataclasses import replace

from app.contracts import SwingTradeAssessment

from .models import SwingTradeContext
from .v17 import metric
from .v19 import SwingTradeEngineV19
from .v110 import SwingTradeEngineV110


class SwingTradeEngineV111(SwingTradeEngineV19):
    engine_version = "1.11.0"
    entry_geometry = staticmethod(SwingTradeEngineV110.entry_geometry)

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.engine_version in {"1.7.0", "1.8.0", "1.9.0", "1.10.0"}
            and metric(previous, "rebound_state") in {"OPEN", "EXITED"}
        ):
            context = replace(
                context,
                previous_assessment=previous.model_copy(
                    update={"engine_version": self.engine_version}
                ),
            )
        return self.entry_geometry(super().analyze(context))
