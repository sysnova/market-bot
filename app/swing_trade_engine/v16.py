"""Observe recovery quality alongside the unchanged native ST1-ST4 decision."""

import hashlib
import json

from app.contracts import BarTimeframe, NamedValue, SwingTradeAssessment, SwingTradeMaturity

from .models import SwingTradeContext
from .recovery_quality import local_breakout, macd_metrics
from .v14 import SwingTradeEngineV14


class SwingTradeEngineV16(SwingTradeEngineV14):
    """MACD and local structure are evidence, not a new maturity or veto."""

    engine_version = "1.6.0"

    def __init__(
        self,
        *,
        macd_fast_period: int = 12,
        macd_slow_period: int = 26,
        macd_signal_period: int = 9,
        macd_max_age_hours: int = 96,
        recovery_reference_bars: int = 2,
        **kwargs: object,
    ) -> None:
        if not 1 <= macd_fast_period < macd_slow_period or macd_signal_period < 1:
            raise ValueError("MACD periods must be positive with fast < slow")
        if macd_max_age_hours < 1 or recovery_reference_bars < 1:
            raise ValueError("MACD freshness and recovery window must be positive")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._macd_fast = macd_fast_period
        self._macd_slow = macd_slow_period
        self._macd_signal = macd_signal_period
        self._macd_age = macd_max_age_hours
        self._reference_bars = recovery_reference_bars

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        native = super().analyze(context)
        at = context.current_price_at or context.as_of
        metrics: list[NamedValue] = [
            NamedValue(name="recovery_quality_mode", value="OBSERVATION"),
            NamedValue(name="macd_fast_period", value=self._macd_fast),
            NamedValue(name="macd_slow_period", value=self._macd_slow),
            NamedValue(name="macd_signal_period", value=self._macd_signal),
        ]
        for prefix, bars, timeframe in (
            (
                "daily",
                context.momentum_daily_bars
                if context.momentum_daily_bars is not None
                else context.daily_bars,
                BarTimeframe.DAY_1,
            ),
            ("4h", context.four_hour_bars, BarTimeframe.HOUR_4),
        ):
            metrics.extend(
                macd_metrics(
                    bars,
                    symbol=context.symbol,
                    timeframe=timeframe,
                    as_of=at,
                    prefix=prefix,
                    fast=self._macd_fast,
                    slow=self._macd_slow,
                    signal=self._macd_signal,
                    max_age_hours=self._macd_age,
                )
            )
        breakout, reference = local_breakout(
            context.confirmation_bars, as_of=at, reference_bars=self._reference_bars
        )
        values = {m.name: m.value for m in metrics}
        quality = "WATCHING"
        if native.maturity in {SwingTradeMaturity.ST3, SwingTradeMaturity.ST4}:
            quality = "EARLY_REACTION"
            if breakout:
                quality = "LOCAL_BREAKOUT"
                if values["macd_4h_direction"] == "IMPROVING":
                    quality = "RECOVERY_WITH_MOMENTUM"
        metrics.extend(
            (
                NamedValue(name="recovery_quality", value=quality),
                NamedValue(name="recovery_local_breakout", value=breakout),
                NamedValue(name="recovery_reference_price", value=reference),
                NamedValue(name="recovery_reference_bars", value=self._reference_bars),
            )
        )
        fingerprint = json.dumps(
            [m.model_dump(mode="json") for m in metrics], sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(f"{native.context_hash}|{fingerprint}".encode()).hexdigest()
        return native.model_copy(
            update={
                "engine_version": self.engine_version,
                "metrics": (*native.metrics, *metrics),
                "reasons": (*native.reasons, f"recovery_observation_{quality.lower()}"),
                "context_hash": f"sha256:{digest}",
            }
        )
