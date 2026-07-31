"""Version-pinned Entry Watcher v3 confirmation policy."""

from __future__ import annotations

from datetime import datetime

from app.contracts import AnalysisHorizon, AnalysisResult

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


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
