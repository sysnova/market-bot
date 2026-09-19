"""Confirm exceptional breakdown impulses without waiting for an unavailable retest."""

from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue, PatternDirection

from .models import IntradayContext
from .v9 import IntradayEngineV9

_WAIT_REASONS = {
    "short_late_entry_wait_retest",
    "short_mature_retest_pending",
    "short_lower_high_pending",
    "short_breakdown_persistence_pending",
    "short_ema20_retest_required",
    "short_confirmation_gate_pending",
}


class IntradayEngineV10(IntradayEngineV9):
    """Add a strict high-volume impulse lane for extended bearish breakdowns."""

    engine_version = "10.0.0"

    def __init__(
        self,
        *,
        short_impulse_breakdown_enabled: bool = True,
        short_impulse_minimum_momentum_percent: Decimal = Decimal("0.40"),
        short_impulse_minimum_rvol: Decimal = Decimal("2.50"),
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        if short_impulse_minimum_momentum_percent <= 0 or short_impulse_minimum_rvol <= 0:
            raise ValueError("impulse SHORT thresholds must be positive")
        self._short_impulse_breakdown_enabled = short_impulse_breakdown_enabled
        self._short_impulse_minimum_momentum_percent = (
            short_impulse_minimum_momentum_percent
        )
        self._short_impulse_minimum_rvol = short_impulse_minimum_rvol

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {item.name: item.value for item in result.metrics}
        momentum = metrics.get("momentum_5_percent")
        relative_volume = metrics.get("relative_volume")
        impulse = (
            self._short_confirmation_enabled
            and self._short_impulse_breakdown_enabled
            and result.direction is PatternDirection.BEARISH
            and metrics.get("setup") == "bearish_breakdown"
            and metrics.get("intraday_regime") == "bearish_trend"
            and metrics.get("confirmation_quality") == "strong"
            and metrics.get("risk_ok") is True
            and metrics.get("short_entry_lane") == "STANDARD"
            and metrics.get("short_entry_efficiency_gate_passed") is False
            and metrics.get("short_mature_confirmation_gate_passed") is not True
            and isinstance(momentum, Decimal)
            and momentum <= -self._short_impulse_minimum_momentum_percent
            and isinstance(relative_volume, Decimal)
            and relative_volume >= self._short_impulse_minimum_rvol
        )
        metrics.update(
            short_impulse_breakdown_gate_passed=impulse,
            short_impulse_minimum_momentum_percent=(
                self._short_impulse_minimum_momentum_percent
            ),
            short_impulse_minimum_rvol=self._short_impulse_minimum_rvol,
        )
        if not impulse:
            return result.model_copy(
                update={
                    "engine_version": self.engine_version,
                    "metrics": tuple(
                        NamedValue(name=name, value=value) for name, value in metrics.items()
                    ),
                }
            )

        reasons = [reason for reason in result.reasons if reason not in _WAIT_REASONS]
        reasons.extend(
            (
                "short_impulse_breakdown_confirmed",
                "short_retest_waived_for_impulse",
            )
        )
        metrics.update(
            short_mature_confirmation_gate_passed=True,
            confirmation_gate_passed=True,
            mature_confirmation_gate_passed=True,
            short_entry_timing="confirmed_impulse_breakdown",
            entry_timing="confirmed_impulse_breakdown",
            short_entry_lane="IMPULSE_BREAKDOWN",
        )
        score = max(result.score, Decimal("70"))
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": AnalysisVerdict.FAVORABLE,
                "score": score,
                "confidence": (score / Decimal("100")).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": tuple(
                    NamedValue(name=name, value=value) for name, value in metrics.items()
                ),
            }
        )
