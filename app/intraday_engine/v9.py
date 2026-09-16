"""Require independent persistence and extension gates for standard SHORT entries."""

from decimal import Decimal
from uuid import UUID

from app.contracts import (
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

from .models import IntradayContext
from .v8 import IntradayEngineV8

HUNDRED = Decimal("100")
_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class IntradayEngineV9(IntradayEngineV8):
    """Do not call the first extended breakdown close a mature standard retest."""

    engine_version = "9.0.0"

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {item.name: item.value for item in result.metrics}
        standard = (
            result.direction is PatternDirection.BEARISH
            and metrics.get("setup") in _BEARISH_SETUPS
            and metrics.get("short_entry_lane") == "STANDARD"
        )
        if not standard:
            return result.model_copy(update={"engine_version": self.engine_version})

        persistent = metrics.get("short_confirmation_persistence") is True
        extension_valid = metrics.get("short_ema20_extension_warning") is not True
        confirmed = (
            metrics.get("short_mature_confirmation_gate_passed") is True
            and persistent
            and extension_valid
        )
        metrics["short_standard_confirmation_gate_passed"] = confirmed
        if confirmed:
            return result.model_copy(
                update={
                    "engine_version": self.engine_version,
                    "metrics": tuple(
                        NamedValue(name=name, value=value) for name, value in metrics.items()
                    ),
                }
            )

        reasons = list(result.reasons)
        if not persistent:
            reasons.append("short_breakdown_persistence_pending")
        if not extension_valid:
            reasons.append("short_ema20_retest_required")
        metrics.update(
            short_mature_confirmation_gate_passed=False,
            short_mature_retest_confirmed=False,
            short_entry_timing="wait_short_retest",
            confirmation_gate_passed=False,
            mature_confirmation_gate_passed=False,
            mature_retest_confirmed=False,
            entry_timing="wait_short_retest",
        )
        score = min(result.score, Decimal("64"))
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": AnalysisVerdict.WATCH,
                "score": score,
                "confidence": (score / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": tuple(
                    NamedValue(name=name, value=value) for name, value in metrics.items()
                ),
            }
        )
