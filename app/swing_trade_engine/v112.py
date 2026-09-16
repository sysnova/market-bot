# pyright: reportPrivateUsage=false
"""Require a mature secondary retest when completed daily candles show damage."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from itertools import pairwise

from app.contracts import MarketBar, NamedValue, SwingTradeAssessment

from .engine import _upsert_metrics
from .models import SwingTradeContext
from .v17 import metric
from .v111 import SwingTradeEngineV111


class SwingTradeEngineV112(SwingTradeEngineV111):
    engine_version = "1.12.0"

    def __init__(
        self,
        *,
        damaged_daily_fast_sessions: int = 20,
        damaged_daily_slow_sessions: int = 50,
        damaged_daily_recent_low_sessions: int = 3,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if not 1 < damaged_daily_fast_sessions < damaged_daily_slow_sessions:
            raise ValueError("Damaged daily sessions require 1 < fast < slow")
        if damaged_daily_recent_low_sessions < 1:
            raise ValueError("Damaged daily recent-low sessions must be positive")
        self._daily_fast = damaged_daily_fast_sessions
        self._daily_slow = damaged_daily_slow_sessions
        self._daily_recent_low = damaged_daily_recent_low_sessions

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        previous = context.previous_assessment
        if (
            previous is not None
            and previous.engine_version
            in {"1.7.0", "1.8.0", "1.9.0", "1.10.0", "1.11.0"}
            and metric(previous, "rebound_state") in {"OPEN", "EXITED"}
        ):
            context = replace(
                context,
                previous_assessment=previous.model_copy(
                    update={"engine_version": self.engine_version}
                ),
            )

        damaged, fast_sma, slow_sma, recent_low = self._daily_structure(context)
        result = super().analyze(context)
        state = metric(result, "rebound_state")
        stages_passed = (
            2
            if state in {"OPEN", "EXITED", "ACCEPTED_NOT_ACTIONABLE"}
            else 1
            if state == "BREAKOUT"
            else 0
        )
        observations = (
            NamedValue(name="rebound_daily_structure_damaged", value=damaged),
            NamedValue(name="rebound_daily_fast_sma", value=fast_sma),
            NamedValue(name="rebound_daily_slow_sma", value=slow_sma),
            NamedValue(name="rebound_daily_recent_low", value=recent_low),
            NamedValue(name="rebound_confirmation_stages_required", value=2),
            NamedValue(name="rebound_confirmation_stages_passed", value=stages_passed),
            NamedValue(
                name="rebound_secondary_closes_required",
                value=self._rising_closes if damaged else 1,
            ),
        )
        reasons = result.reasons
        if damaged:
            reasons = (*reasons, "rebound_damaged_daily_structure_mature_retest_required")
        digest = hashlib.sha256(
            json.dumps(
                [result.context_hash, damaged, fast_sma, slow_sma, recent_low],
                default=str,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "metrics": _upsert_metrics(result, *observations),
                "reasons": tuple(dict.fromkeys(reasons)),
                "context_hash": f"sha256:{digest}",
            }
        )

    def _entry_confirmation(
        self,
        context: SwingTradeContext,
        native: SwingTradeAssessment,
        bars: tuple[MarketBar, ...],
        touch: int,
        breakout: int,
        end: int,
        level: Decimal,
        buffer: Decimal,
    ) -> tuple[bool, bool, str | None]:
        damaged, _, _, _ = self._daily_structure(context)
        if not damaged:
            return super()._entry_confirmation(
                context, native, bars, touch, breakout, end, level, buffer
            )

        recent = bars[breakout + 1 : end + 1][-self._rising_closes :]
        confirmed = (
            len(recent) == self._rising_closes
            and all(
                bar.is_final and bar.close > level and bar.low > bars[touch].low
                for bar in recent
            )
            and all(bar.close > prior.close for prior, bar in pairwise(recent))
            and any(bar.low <= level + buffer and bar.high >= level for bar in recent)
            and recent[-1].close > recent[-1].open
        )
        return (
            confirmed,
            False,
            None if confirmed else "rebound_damaged_daily_structure_retest_pending",
        )

    def _daily_structure(
        self, context: SwingTradeContext
    ) -> tuple[bool, Decimal | None, Decimal | None, Decimal | None]:
        bars = tuple(
            bar
            for bar in context.daily_bars
            if bar.is_final and bar.timestamp <= context.as_of
        )
        if len(bars) < self._daily_slow:
            return False, None, None, None
        fast_sma = sum(
            (bar.close for bar in bars[-self._daily_fast :]), Decimal(0)
        ) / Decimal(self._daily_fast)
        slow_sma = sum(
            (bar.close for bar in bars[-self._daily_slow :]), Decimal(0)
        ) / Decimal(self._daily_slow)
        recent_low = min(bar.low for bar in bars[-self._daily_recent_low :])
        damaged = (
            context.current_price < fast_sma and fast_sma <= slow_sma
        ) or context.current_price < recent_low
        return damaged, fast_sma, slow_sma, recent_low
