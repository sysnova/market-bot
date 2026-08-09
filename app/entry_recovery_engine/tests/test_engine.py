from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryHorizonLeg,
    EntryLegStatus,
    EntryMaturityCheckpoint,
    EntryMaturityLevel,
    EntryOpportunity,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EntrySetupAssessment,
    EntrySignalFamily,
    MarketBar,
    NamedValue,
    PatternDirection,
    new_uuid7,
)
from app.entry_recovery_engine import EntryRecoveryEngine, EntryRecoveryEngineV11

NOW = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def test_recovery_emits_after_intraday_stop_when_swing_thesis_reconfirms() -> None:
    engine = EntryRecoveryEngine()
    event = _opportunity_event()
    engine.ingest_opportunity(event)
    engine.ingest_analysis(_analysis(AnalysisHorizon.SWING, NOW - timedelta(hours=2)))
    engine.ingest_analysis(_analysis(AnalysisHorizon.INTRADAY, NOW - timedelta(minutes=2)))

    signal = engine.ingest_bar(_bar(Decimal("243.50")))

    assert signal is not None
    assert signal.family is EntrySignalFamily.CORE_RECOVERY
    assert signal.maturity is EntryMaturityLevel.L4
    assert signal.setup_id == f"recovery:{event.opportunity.opportunity_id}"
    assert signal.entry_price == Decimal("243.50")
    assert event.event_id in signal.source_event_ids


def test_recovery_waits_for_fresh_intraday_higher_low() -> None:
    engine = EntryRecoveryEngine()
    engine.ingest_opportunity(_opportunity_event())
    engine.ingest_analysis(_analysis(AnalysisHorizon.SWING, NOW - timedelta(hours=2)))
    engine.ingest_analysis(
        _analysis(
            AnalysisHorizon.INTRADAY,
            NOW - timedelta(minutes=2),
            higher_low=False,
        )
    )

    assert engine.ingest_bar(_bar(Decimal("243.50"))) is None


def test_recovery_does_not_reopen_broken_or_closed_thesis() -> None:
    engine = EntryRecoveryEngine()
    event = _opportunity_event().model_copy(
        update={
            "opportunity": _opportunity_event().opportunity.model_copy(
                update={
                    "status": EntryOpportunityStatus.CLOSED,
                    "closed_at": NOW,
                    "close_reason": "ORIGINAL_THESIS_INVALIDATED",
                }
            )
        }
    )
    engine.ingest_opportunity(event)
    engine.ingest_analysis(_analysis(AnalysisHorizon.SWING, NOW - timedelta(hours=2)))
    engine.ingest_analysis(_analysis(AnalysisHorizon.INTRADAY, NOW - timedelta(minutes=2)))

    assert engine.ingest_bar(_bar(Decimal("243.50"))) is None


def test_recovery_signal_is_idempotent_per_opportunity() -> None:
    engine = EntryRecoveryEngine()
    engine.ingest_opportunity(_opportunity_event())
    engine.ingest_analysis(_analysis(AnalysisHorizon.SWING, NOW - timedelta(hours=2)))
    engine.ingest_analysis(_analysis(AnalysisHorizon.INTRADAY, NOW - timedelta(minutes=2)))

    assert engine.ingest_bar(_bar(Decimal("243.50"))) is not None
    assert engine.ingest_bar(_bar(Decimal("244.00"))) is None


def test_v11_emits_source_agnostic_assessment_without_assigning_l4() -> None:
    engine = EntryRecoveryEngineV11()
    event = _opportunity_event()
    engine.ingest_opportunity(event)
    engine.ingest_analysis(_analysis(AnalysisHorizon.SWING, NOW - timedelta(hours=2)))
    engine.ingest_analysis(_analysis(AnalysisHorizon.INTRADAY, NOW - timedelta(minutes=2)))

    assessment = engine.ingest_assessment(_bar(Decimal("243.50")))

    assert isinstance(assessment, EntrySetupAssessment)
    assert assessment.family is EntrySignalFamily.CORE_RECOVERY
    assert "maturity" not in EntrySetupAssessment.model_fields
    assert assessment.setup_id == f"recovery:{event.opportunity.opportunity_id}"
    assert tuple(item.horizon for item in assessment.component_analyses) == (
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    )


def _opportunity_event() -> EntryOpportunityEvent:
    source_id = new_uuid7()
    checkpoint = EntryMaturityCheckpoint(
        level=EntryMaturityLevel.L2,
        reached_at=NOW - timedelta(days=1),
        entry_price=Decimal("243.39"),
        current_price=Decimal("242.78"),
        highest_price=Decimal("243.39"),
        lowest_price=Decimal("242.50"),
        invalidation=Decimal("242.78"),
    )
    intraday = EntryHorizonLeg(
        horizon=AnalysisHorizon.INTRADAY,
        status=EntryLegStatus.INVALIDATED,
        opened_at=NOW - timedelta(days=1),
        entry_price=Decimal("243.39"),
        current_price=Decimal("242.78"),
        invalidation=Decimal("242.78"),
        target=Decimal("244.30"),
        highest_price=Decimal("243.39"),
        lowest_price=Decimal("242.78"),
        closed_at=NOW - timedelta(days=1) + timedelta(minutes=1),
        exit_price=Decimal("242.78"),
        gain_loss_percent=Decimal("-0.25"),
        mae_percent=Decimal("-0.25"),
    )
    swing = EntryHorizonLeg(
        horizon=AnalysisHorizon.SWING,
        status=EntryLegStatus.OPEN,
        opened_at=NOW - timedelta(days=1),
        entry_price=Decimal("243.39"),
        current_price=Decimal("246"),
        invalidation=Decimal("238.59"),
        target=Decimal("251.08"),
        highest_price=Decimal("247"),
        lowest_price=Decimal("242.50"),
    )
    opportunity = EntryOpportunity(
        symbol="TTWO",
        status=EntryOpportunityStatus.OPEN,
        current_maturity=EntryMaturityLevel.L2,
        peak_maturity=EntryMaturityLevel.L2,
        progress_percent=Decimal("75"),
        original_watch_id=new_uuid7(),
        armed_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(days=30),
        zone_low=Decimal("206"),
        zone_high=Decimal("236.28"),
        invalidation=Decimal("199.82"),
        original_price=Decimal("236.53"),
        current_price=Decimal("246"),
        source_analysis_ids=(source_id,),
        legs=(swing, intraday),
        checkpoints=(checkpoint,),
    )
    return EntryOpportunityEvent(
        occurred_at=NOW - timedelta(minutes=5),
        opportunity=opportunity,
        reasons=("intraday_leg_invalidated",),
    )


def _analysis(
    horizon: AnalysisHorizon,
    as_of: datetime,
    *,
    higher_low: bool = True,
) -> AnalysisResult:
    metrics = (NamedValue(name="setup", value="bullish_entry_confirmation"),)
    if horizon is AnalysisHorizon.INTRADAY:
        metrics = (
            *metrics,
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=higher_low),
        )
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="TTWO",
        horizon=horizon,
        as_of=as_of,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        score=Decimal("70"),
        confidence=Decimal("0.7"),
        reasons=("bullish_structure",),
        metrics=metrics,
        context_hash=HASH,
    )


def _bar(price: Decimal) -> MarketBar:
    return MarketBar(
        symbol="TTWO",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW,
        open=price,
        high=price + Decimal("0.10"),
        low=price - Decimal("0.10"),
        close=price,
        volume=Decimal("10000"),
        source="alpaca",
        feed="iex",
    )
