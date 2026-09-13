# pyright: reportPrivateUsage=false
"""Require a bullish retest with rising closes after a post-breakout pullback."""

from dataclasses import replace
from decimal import Decimal
from itertools import pairwise

from app.contracts import MarketBar, SwingTradeAssessment

from .models import SwingTradeContext
from .v17 import SwingTradeEngineV17, metric


class SwingTradeEngineV18(SwingTradeEngineV17):
    engine_version = "1.8.0"

    def __init__(self, *, rebound_rising_closes: int = 3, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if rebound_rising_closes < 2:
            raise ValueError("Rebound rising closes must be at least two")
        self._rising_closes = rebound_rising_closes

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.engine_version == "1.7.0"
            and metric(previous, "rebound_state") in {"OPEN", "EXITED"}
        ):
            # Preserve open risk and the reentry cutoff when upgrading the entry policy.
            context = replace(
                context,
                previous_assessment=previous.model_copy(
                    update={"engine_version": self.engine_version}
                ),
            )
        return super().analyze(context)

    def _entry_acceptance(
        self,
        bars: tuple[MarketBar, ...],
        touch: int,
        breakout: int,
        end: int,
        level: Decimal,
        buffer: Decimal,
    ) -> tuple[bool, bool, str | None]:
        reaction = bars[touch : end + 1]
        if not any(b.close < a.close for a, b in pairwise(reaction)):
            return super()._entry_acceptance(bars, touch, breakout, end, level, buffer)

        # A later accepted hour cannot erase the pullback for this breakout candidate.
        recent = bars[breakout : end + 1][-self._rising_closes :]
        confirmed = (
            len(recent) == self._rising_closes
            and all(b.close > a.close for a, b in pairwise(recent))
            and all(b.close > level for b in recent)
            and any(b.low <= level + buffer for b in recent)
            and recent[-1].close > recent[-1].open
        )
        return confirmed, False, None if confirmed else "rebound_bullish_retest_pending"
