"""Version-pinned Entry Watcher v3 confirmation policy."""

from __future__ import annotations

from datetime import datetime

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    PatternDirection,
)

from .v2 import EntryWatcherV2


class EntryWatcherV3(EntryWatcherV2):
    """Require v3 Swing/Intraday gates before triggering a persisted thesis."""

    engine_version = "3.0.0"

    def _confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        if not super()._confirmed(analyses, now=now):
            return False
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        return (
            swing.engine_version == "3.0.0"
            and intraday.engine_version == "3.0.0"
            and _metrics(swing).get("anchored_vwap_gate_passed") is True
            and _metrics(intraday).get("confirmation_gate_passed") is True
        )

    def _continuation_confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        if super()._confirmed(analyses, now=now):
            return self._v3_gates_pass(analyses)
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        if not required.issubset(analyses):
            return False
        if any(
            now - analyses[horizon].as_of > self._policy.max_ages[horizon]
            for horizon in required
        ):
            return False
        long_term = analyses[AnalysisHorizon.LONG_TERM]
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        swing_metrics = _metrics(swing)
        intraday_metrics = _metrics(intraday)
        quality = intraday_metrics.get("confirmation_quality")
        return (
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict
            not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and swing.engine_version == "3.0.0"
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.CAUTION}
            and swing_metrics.get("classification")
            in {"breakout", "pullback", "extended"}
            and swing_metrics.get("anchored_vwap_gate_passed") is True
            and intraday.engine_version == "3.0.0"
            and intraday.direction is PatternDirection.BULLISH
            and intraday.verdict is AnalysisVerdict.FAVORABLE
            and intraday_metrics.get("setup")
            in {
                "bullish_breakout",
                "bullish_vwap_reclaim",
                "bullish_entry_confirmation",
            }
            and quality in {None, "standard", "strong"}
            and intraday_metrics.get("confirmation_gate_passed") is True
        )

    @staticmethod
    def _v3_gates_pass(
        analyses: dict[AnalysisHorizon, AnalysisResult],
    ) -> bool:
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        return (
            swing.engine_version == "3.0.0"
            and intraday.engine_version == "3.0.0"
            and _metrics(swing).get("anchored_vwap_gate_passed") is True
            and _metrics(intraday).get("confirmation_gate_passed") is True
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
