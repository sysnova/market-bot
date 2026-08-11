"""Signal Fusion v0.4 with bounded independent volume-structure evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    FusionAssessment,
    PatternDirection,
)

from .engine import SignalFusionEngine
from .models import SignalFusionContext

ZERO = Decimal()
HUNDRED = Decimal("100")


class SignalFusionEngineV04(SignalFusionEngine):
    """Increase confidence score without replacing structural or execution gates."""

    engine_version = "0.4.0"

    def evaluate(self, context: SignalFusionContext) -> FusionAssessment:
        baseline = super().evaluate(context)
        volume = next(
            (
                item
                for item in context.analyses
                if item.horizon is AnalysisHorizon.VOLUME_STRUCTURE
            ),
            None,
        )
        boost = _boost(volume, assessed_at=baseline.occurred_at)
        effective = min(HUNDRED, baseline.score + boost).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        reasons = baseline.reasons
        if boost > ZERO:
            reasons = tuple(
                dict.fromkeys((*reasons, f"volume_structure_boost:+{boost}"))
            )
        return baseline.model_copy(
            update={
                "engine_version": self.engine_version,
                "score": effective,
                "confidence": (effective / HUNDRED).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ),
                "reasons": reasons,
            }
        )


def _boost(result: AnalysisResult | None, *, assessed_at: datetime) -> Decimal:
    if (
        result is None
        or result.direction is not PatternDirection.BULLISH
        or result.verdict not in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
        or assessed_at - result.as_of > timedelta(days=14)
    ):
        return ZERO
    value = next(
        (item.value for item in result.metrics if item.name == "evidence_boost"), ZERO
    )
    if isinstance(value, bool):
        return ZERO
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return ZERO
    return min(Decimal("10"), max(ZERO, parsed))
