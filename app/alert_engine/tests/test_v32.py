from datetime import UTC, datetime
from decimal import Decimal

from app.alert_engine import AlertEngineV32, BuyMaturity, buy_maturity
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryMaturityLevel,
    EntrySetupAssessment,
    EntrySignalFamily,
    PatternDirection,
)

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def test_alert_v32_owns_recovery_quality_and_classifies_swing_intraday_as_l2() -> None:
    assessment = _assessment()

    alert = AlertEngineV32().ingest_setup_assessment(assessment, now=NOW)

    assert alert is not None
    assert alert.alert_id == assessment.assessment_id
    assert alert.kind is AlertKind.ENTRY_CONFIRMED
    assert buy_maturity(alert) is BuyMaturity.SWING_CONFIRMED
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["entry_signal_family"] == EntrySignalFamily.CORE_RECOVERY.value
    assert metrics["entry_maturity"] == EntryMaturityLevel.L2.value
    assert metrics["entry_setup_id"] == assessment.setup_id


def test_alert_v32_rejects_recovery_without_configured_horizon_evidence() -> None:
    assessment = _assessment().model_copy(
        update={
            "horizons": (AnalysisHorizon.INTRADAY,),
            "component_analyses": (_analysis(AnalysisHorizon.INTRADAY),),
        }
    )

    assert AlertEngineV32().ingest_setup_assessment(assessment, now=NOW) is None


def _assessment() -> EntrySetupAssessment:
    return EntrySetupAssessment(
        family=EntrySignalFamily.CORE_RECOVERY,
        symbol="TTWO",
        assessed_at=NOW,
        setup_id="recovery:ttwo",
        entry_price=Decimal("243.50"),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        component_analyses=(
            _analysis(AnalysisHorizon.SWING),
            _analysis(AnalysisHorizon.INTRADAY),
        ),
        zone_low=Decimal("243.39"),
        zone_high=Decimal("243.50"),
        invalidation=Decimal("238.59"),
        targets=(Decimal("251.08"),),
        policy_id="core-recovery",
        policy_version="1.1.0",
        reasons=("tactical_invalidation_recovered",),
    )


def _analysis(horizon: AnalysisHorizon) -> AnalysisResult:
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="TTWO",
        horizon=horizon,
        as_of=NOW,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("80"),
        confidence=Decimal("0.8"),
        reasons=("bullish_structure",),
        context_hash=HASH,
    )
