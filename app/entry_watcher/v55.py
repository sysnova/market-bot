"""Structure-recovery confirmation support for Entry Watcher v5.5."""

from __future__ import annotations

from datetime import datetime

from app.contracts import AnalysisHorizon, AnalysisResult, AnalysisVerdict, PatternDirection

from .v54 import EntryWatcherV54


class EntryWatcherV55(EntryWatcherV54):
    """Accept Swing v8 recovery only when its dedicated gate is auditable."""

    engine_version = "5.5.0"

    def _confirmed(
        self,
        analyses: dict[AnalysisHorizon, AnalysisResult],
        *,
        now: datetime,
    ) -> bool:
        if super()._confirmed(analyses, now=now):
            return True
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
        swing_metrics = _metrics(swing)
        return bool(
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict
            not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict is AnalysisVerdict.FAVORABLE
            and swing_metrics.get("classification") == "recovery"
            and swing_metrics.get("entry_lane") == "STRUCTURE_RECOVERY"
            and swing_metrics.get("recovery_entry_gate_passed") is True
            and self._v4_gates_pass(analyses)
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
