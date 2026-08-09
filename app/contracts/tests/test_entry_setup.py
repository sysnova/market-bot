from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntrySetupAssessment,
    EntrySignalFamily,
    PatternDirection,
    entry_setup_assessment_subject,
)

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def test_entry_setup_assessment_carries_evidence_without_core_maturity() -> None:
    swing = _analysis(AnalysisHorizon.SWING)
    intraday = _analysis(AnalysisHorizon.INTRADAY)

    assessment = EntrySetupAssessment(
        family=EntrySignalFamily.CORE_RECOVERY,
        symbol="TTWO",
        assessed_at=NOW,
        setup_id="recovery:ttwo",
        entry_price=Decimal("243.50"),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analyses=(swing, intraday),
        zone_low=Decimal("243.39"),
        zone_high=Decimal("243.50"),
        invalidation=Decimal("238.59"),
        targets=(Decimal("251.08"),),
        policy_id="core-recovery",
        policy_version="1.1.0",
        reasons=("tactical_invalidation_recovered",),
    )

    assert assessment.assessment_id.version == 7
    assert "maturity" not in EntrySetupAssessment.model_fields
    assert entry_setup_assessment_subject(assessment.family, assessment.symbol) == (
        "marketbot.v1.entry-setup.CORE_RECOVERY.TTWO"
    )


def test_entry_setup_assessment_rejects_evidence_from_another_symbol() -> None:
    with pytest.raises(ValidationError, match="assessment symbol"):
        EntrySetupAssessment(
            family=EntrySignalFamily.CORE_RECOVERY,
            symbol="TTWO",
            assessed_at=NOW,
            setup_id="recovery:ttwo",
            entry_price=Decimal("243.50"),
            horizons=(AnalysisHorizon.SWING,),
            component_analyses=(_analysis(AnalysisHorizon.SWING, symbol="NVDA"),),
            policy_id="core-recovery",
            policy_version="1.1.0",
            reasons=("candidate",),
        )


def _analysis(horizon: AnalysisHorizon, *, symbol: str = "TTWO") -> AnalysisResult:
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol=symbol,
        horizon=horizon,
        as_of=NOW,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("70"),
        confidence=Decimal("0.8"),
        reasons=("bullish_structure",),
        context_hash=HASH,
    )
