"""Anchored-VWAP confirmation gate for Swing v3."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import SwingContext
from .v2 import SwingEngineV2


class SwingEngineV3(SwingEngineV2):
    """Do not call a pullback favorable while both anchored VWAPs remain overhead."""

    engine_version = "3.0.0"

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {item.name: item.value for item in result.metrics}
        pivot = metrics.get("price_vs_pivot_low_avwap_percent")
        breakout = metrics.get("price_vs_breakout_avwap_percent")
        both_overhead = (
            isinstance(pivot, Decimal)
            and isinstance(breakout, Decimal)
            and pivot < 0
            and breakout < 0
        )
        classification = str(metrics.get("classification", "setup"))
        gate_passed = not both_overhead
        if not both_overhead or classification not in {"pullback", "breakout"}:
            return _tag(result, gate_passed=gate_passed)
        return result.model_copy(
            update={
                "verdict": AnalysisVerdict.WATCH,
                "score": min(result.score, Decimal("64.00")),
                "confidence": min(result.confidence, Decimal("0.6400")),
                "reasons": (*result.reasons, "v3_anchored_vwap_confirmation_pending"),
                "metrics": _upsert(
                    result,
                    NamedValue(name="classification", value="setup"),
                    NamedValue(name="anchored_vwap_gate_passed", value=False),
                    NamedValue(name="entry_confirmation_rule_version", value=self.engine_version),
                ),
            }
        )


def _tag(result: AnalysisResult, *, gate_passed: bool) -> AnalysisResult:
    return result.model_copy(
        update={
            "metrics": _upsert(
                result,
                NamedValue(name="anchored_vwap_gate_passed", value=gate_passed),
                NamedValue(name="entry_confirmation_rule_version", value="3.0.0"),
            )
        }
    )


def _upsert(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
