"""Recovery assessment output that leaves L1-L4 ownership to Alert."""

from __future__ import annotations

from app.contracts import AnalysisHorizon, EntrySetupAssessment, MarketBar

from .engine import EntryRecoveryEngine


class EntryRecoveryEngineV11(EntryRecoveryEngine):
    """Preserve v1 recovery rules while emitting evidence instead of maturity."""

    engine_version = "1.1.0"

    def ingest_assessment(self, bar: MarketBar) -> EntrySetupAssessment | None:
        legacy_signal = super().ingest_bar(bar)
        if legacy_signal is None:
            return None
        analyses = self._analyses[bar.symbol]
        components = (
            analyses[AnalysisHorizon.SWING],
            analyses[AnalysisHorizon.INTRADAY],
        )
        return EntrySetupAssessment(
            assessment_id=legacy_signal.signal_id,
            family=legacy_signal.family,
            symbol=legacy_signal.symbol,
            assessed_at=legacy_signal.created_at,
            setup_id=legacy_signal.setup_id,
            entry_price=legacy_signal.entry_price,
            horizons=legacy_signal.horizons,
            component_analyses=components,
            zone_low=legacy_signal.zone_low,
            zone_high=legacy_signal.zone_high,
            invalidation=legacy_signal.invalidation,
            targets=legacy_signal.targets,
            policy_id=legacy_signal.policy_id,
            policy_version=legacy_signal.policy_version,
            reasons=legacy_signal.reasons,
            source_event_ids=legacy_signal.source_event_ids,
        )
