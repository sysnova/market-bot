# pyright: reportPrivateUsage=false
"""Expose the exact recovery trigger and rebound used by the confirming Swing."""

from uuid import UUID

from app.contracts import AnalysisResult, NamedValue

from .models import SwingContext
from .v15 import SwingEngineV15


class SwingEngineV16(SwingEngineV15):
    engine_version = "16.0.0"

    def analyze(
        self, context: SwingContext, *, source_event_ids: tuple[UUID, ...] = ()
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {m.name: m.value for m in result.metrics}
        if metrics.get("recovery_entry_gate_passed") is not True:
            return result
        # These are the same completed bars accepted by the inherited recovery gate.
        bars = context.intraday_bars[-self._recovery_intraday_confirmation_bars :]
        previous = bars[-(self._recovery_intraday_breakout_lookback_bars + 1) : -1]
        return result.model_copy(
            update={
                "metrics": (
                    *result.metrics,
                    NamedValue(name="recovery_breakout_level", value=max(b.high for b in previous)),
                    NamedValue(
                        name="recovery_intraday_rebound_low",
                        value=min(b.low for b in bars[len(bars) // 2 :]),
                    ),
                    NamedValue(name="recovery_confirmation_bar_at", value=bars[-1].timestamp),
                )
            }
        )
