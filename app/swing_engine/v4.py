"""Reward/risk-aware Swing entries while preserving v3 for replay."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import SwingContext
from .v3 import SwingEngineV3

DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE = Decimal("1.50")


class SwingEngineV4(SwingEngineV3):
    """Keep bullish structure visible without calling a late entry actionable."""

    engine_version = "4.0.0"

    def __init__(
        self,
        *,
        anchored_vwap_gate: bool = True,
        minimum_reward_risk_to_resistance: Decimal = (
            DEFAULT_MINIMUM_REWARD_RISK_TO_RESISTANCE
        ),
        strategy_version: str = "4.0.0",
    ) -> None:
        super().__init__(
            anchored_vwap_gate=anchored_vwap_gate,
            strategy_version=strategy_version,
        )
        if minimum_reward_risk_to_resistance <= 0:
            raise ValueError("minimum_reward_risk_to_resistance must be positive")
        self._minimum_reward_risk_to_resistance = minimum_reward_risk_to_resistance

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = {item.name: item.value for item in result.metrics}
        classification = str(metrics.get("classification", "setup"))
        reward_risk = metrics.get("reward_risk_to_resistance")
        anchored_vwap_passed = metrics.get("anchored_vwap_gate_passed") is True

        # Clearing the historical resistance does not create new upside by itself.
        # Breakouts remain non-actionable until an overhead level offers real R/R.
        reward_risk_passed = (
            isinstance(reward_risk, Decimal)
            and reward_risk >= self._minimum_reward_risk_to_resistance
        )
        entry_gate_passed = (
            anchored_vwap_passed
            and classification in {"pullback", "breakout"}
            and reward_risk_passed
        )
        tagged = result.model_copy(
            update={
                "engine_version": self.engine_version,
                "metrics": _upsert(
                    result,
                    NamedValue(name="swing_entry_gate_passed", value=entry_gate_passed),
                    NamedValue(
                        name="minimum_reward_risk_to_resistance",
                        value=self._minimum_reward_risk_to_resistance,
                    ),
                ),
            }
        )
        if classification not in {"pullback", "breakout"} or reward_risk_passed:
            return tagged
        return tagged.model_copy(
            update={
                "verdict": AnalysisVerdict.WATCH,
                "score": min(tagged.score, Decimal("64.00")),
                "confidence": min(tagged.confidence, Decimal("0.6400")),
                "reasons": tuple(
                    dict.fromkeys(
                        (*tagged.reasons, "insufficient_reward_risk_to_resistance")
                    )
                ),
            }
        )


def _upsert(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
