"""Efficient early SHORT breakdowns with explicit quality diagnostics."""

from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue, PatternDirection

from .models import IntradayContext
from .v7 import IntradayEngineV7


class IntradayEngineV8(IntradayEngineV7):
    """Accept standard candles only with independent trend, momentum and volume."""

    engine_version = "8.0.0"

    def __init__(
        self,
        *,
        short_early_breakdown_enabled: bool = True,
        short_early_minimum_momentum_percent: Decimal = Decimal("0.50"),
        short_early_minimum_rvol: Decimal = Decimal("1.30"),
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        if short_early_minimum_momentum_percent <= 0 or short_early_minimum_rvol <= 0:
            raise ValueError("early SHORT thresholds must be positive")
        self._early_enabled = short_early_breakdown_enabled
        self._early_momentum = short_early_minimum_momentum_percent
        self._early_rvol = short_early_minimum_rvol

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {m.name: m.value for m in result.metrics}
        momentum = metrics.get("momentum_5_percent")
        rvol = metrics.get("relative_volume")
        early = (
            self._short_confirmation_enabled
            and self._early_enabled
            and result.direction is PatternDirection.BEARISH
            and metrics.get("setup") == "bearish_breakdown"
            and metrics.get("confirmation_quality") == "standard"
            and metrics.get("intraday_regime") == "bearish_trend"
            and metrics.get("five_minute_lower_high") is True
            and metrics.get("short_confirmation_gate_passed") is True
            and metrics.get("short_entry_efficiency_gate_passed") is True
            and metrics.get("risk_ok") is True
            and metrics.get("short_mature_confirmation_gate_passed") is not True
            and isinstance(momentum, Decimal)
            and momentum <= -self._early_momentum
            and isinstance(rvol, Decimal)
            and rvol >= self._early_rvol
        )
        reasons = list(result.reasons)
        if "short_mature_retest_pending" in reasons:
            reasons.remove("short_mature_retest_pending")
            if metrics.get("five_minute_lower_high") is not True:
                reasons.append("short_lower_high_pending")
            if metrics.get("confirmation_quality") != "strong" and not early:
                reasons.append("short_quality_pending")
        metrics.update(
            short_early_breakdown_gate_passed=early,
            short_early_minimum_momentum_percent=self._early_momentum,
            short_early_minimum_rvol=self._early_rvol,
        )
        if early:
            reasons.append("short_early_breakdown_confirmed")
            metrics.update(
                short_mature_confirmation_gate_passed=True,
                confirmation_gate_passed=True,
                mature_confirmation_gate_passed=True,
                short_entry_timing="confirmed_early_breakdown",
                entry_timing="confirmed_early_breakdown",
                short_entry_lane="EARLY_BREAKDOWN",
            )
        elif "short_quality_pending" in reasons and metrics.get("five_minute_lower_high") is True:
            metrics.update(
                short_entry_timing="wait_short_quality", entry_timing="wait_short_quality"
            )
        score = max(result.score, Decimal("70")) if early else result.score
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": AnalysisVerdict.FAVORABLE if early else result.verdict,
                "score": score,
                "confidence": (score / Decimal("100")).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": tuple(NamedValue(name=k, value=v) for k, v in metrics.items()),
            }
        )
