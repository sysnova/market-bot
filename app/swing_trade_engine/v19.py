# pyright: reportPrivateUsage=false
"""Accept a confirmed local support retest when daily support is distant."""

from dataclasses import replace
from decimal import Decimal
from itertools import pairwise

from app.contracts import MarketBar, SwingTradeAssessment

from .models import SwingTradeContext
from .v17 import metric
from .v18 import SwingTradeEngineV18


class SwingTradeEngineV19(SwingTradeEngineV18):
    engine_version = "1.9.0"

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.engine_version in {"1.7.0", "1.8.0"}
            and metric(previous, "rebound_state") in {"OPEN", "EXITED"}
        ):
            context = replace(
                context,
                previous_assessment=previous.model_copy(
                    update={"engine_version": self.engine_version}
                ),
            )
        return super().analyze(context)

    def _entry_support(
        self,
        native: SwingTradeAssessment,
        bars: tuple[MarketBar, ...],
        touch: int,
        breakout: int,
        end: int,
        level: Decimal,
        buffer: Decimal,
    ) -> str | None:
        if native.support_confluence:
            return "DAILY_CONFLUENCE"
        # Require completed acceptance bars after the breakout; the breakout
        # candle cannot serve as its own retest or a higher-low confirmation.
        recent = bars[breakout + 1 : end + 1][-self._rising_closes :]
        if (
            len(recent) == self._rising_closes
            and all(b.is_final and b.close > level and b.low > bars[touch].low for b in recent)
            and all(b.close > a.close for a, b in pairwise(recent))
            and any(b.low <= level + buffer and b.high >= level for b in recent)
            and recent[-1].close > recent[-1].open
        ):
            return "LOCAL_RETEST"
        return None
