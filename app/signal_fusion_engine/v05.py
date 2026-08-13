"""Signal Fusion v0.5 with bounded fresh Options Gamma evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import AnalysisHorizon, AnalysisResult, FusionAssessment

from .models import SignalFusionContext
from .v04 import SignalFusionEngineV04

ZERO = Decimal()
HUNDRED = Decimal("100")


class SignalFusionEngineV05(SignalFusionEngineV04):
    """Adjust score while preserving every structural and execution gate."""

    engine_version = "0.5.0"

    def evaluate(self, context: SignalFusionContext) -> FusionAssessment:
        baseline = super().evaluate(context)
        gamma = next(
            (
                item
                for item in context.analyses
                if item.horizon is AnalysisHorizon.OPTIONS_GAMMA
            ),
            None,
        )
        usable = _usable_gamma(
            gamma,
            current_price=baseline.current_price,
            now=baseline.occurred_at,
        )
        if usable is None:
            return baseline.model_copy(update={"engine_version": self.engine_version})
        delta, gamma_reasons = _gamma_delta(usable, baseline.current_price)
        effective = min(HUNDRED, max(ZERO, baseline.score + delta)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        return baseline.model_copy(
            update={
                "engine_version": self.engine_version,
                "score": effective,
                "confidence": (effective / HUNDRED).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ),
                "reasons": tuple(
                    dict.fromkeys((*baseline.reasons, *gamma_reasons))
                ),
            }
        )


def _usable_gamma(
    result: AnalysisResult | None, *, current_price: Decimal, now: datetime
) -> AnalysisResult | None:
    if result is None:
        return None
    status = _metric(result, "gamma_status")
    quality = _decimal(_metric(result, "gamma_quality_score"))
    expires_at = _metric(result, "expires_at")
    reference = _decimal(_metric(result, "reference_price"))
    if (
        status != "AVAILABLE"
        or quality is None
        or quality < Decimal("70")
        or not isinstance(expires_at, datetime)
        or expires_at <= now
        or reference is None
        or abs(current_price - reference) / reference * HUNDRED > Decimal("1")
    ):
        return None
    return result


def _gamma_delta(
    result: AnalysisResult, current_price: Decimal
) -> tuple[Decimal, tuple[str, ...]]:
    delta = ZERO
    reasons: list[str] = []
    regime = _metric(result, "gamma_regime")
    call_wall = _decimal(_metric(result, "gamma_call_wall"))
    put_wall = _decimal(_metric(result, "gamma_put_wall"))
    if _metric(result, "gamma_pin_risk") is True:
        delta -= Decimal("4")
        reasons.append("options_gamma_pin_risk:-4")
    if call_wall is not None and current_price < call_wall and _near(current_price, call_wall):
        delta -= Decimal("6")
        reasons.append("options_gamma_call_wall_limits_upside:-6")
    if put_wall is not None and current_price >= put_wall and _near(current_price, put_wall):
        delta += Decimal("4")
        reasons.append("options_gamma_put_wall_support:+4")
    if regime == "NEGATIVE" and _metric(result, "gamma_acceleration_risk") is True:
        if put_wall is not None and current_price <= put_wall:
            delta -= Decimal("8")
            reasons.append("options_gamma_negative_breakdown_risk:-8")
        elif call_wall is not None and current_price >= call_wall:
            delta += Decimal("3")
            reasons.append("options_gamma_negative_breakout_acceleration:+3")
    return max(Decimal("-8"), min(Decimal("6"), delta)), tuple(reasons)


def _metric(result: AnalysisResult, name: str) -> object | None:
    return next((item.value for item in result.metrics if item.name == name), None)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > ZERO else None


def _near(price: Decimal, level: Decimal) -> bool:
    return abs(price - level) / level * HUNDRED <= Decimal("1")
